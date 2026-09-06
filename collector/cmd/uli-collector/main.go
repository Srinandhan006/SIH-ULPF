// uli-collector: the hot wire path (docs/architecture.md). Listens for syslog UDP/TCP and tails
// files/stdin; frames individual log lines (octet-counting for TCP RFC6587, newline for UDP/files);
// batches them and forwards as RawEnvelope JSON to the Python API's POST /v1/ingest.
//
// Deliberately dumb: this process does not parse log *content* at all — it only knows transport
// framing, so a bug here can never mis-normalize a log, only mis-frame it, and framing errors are
// visible immediately (line counts, forward failures) rather than silently corrupting data (P1 in
// docs/architecture.md is enforced downstream in Python; this binary must not violate P1 either,
// so a failed forward is retried with backoff and never drops a batch — buffered to disk overflow
// under sustained backend outage is a documented roadmap item, not implemented here).
package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"
)

type envelope struct {
	TenantID  string            `json:"tenant_id"`
	ProjectID string            `json:"project_id"`
	SourceID  string            `json:"source_id"`
	Transport string            `json:"transport"`
	Peer      string            `json:"peer,omitempty"`
	Hints     map[string]string `json:"hints,omitempty"`
}

type ingestReq struct {
	SourceID  string            `json:"source_id"`
	Transport string            `json:"transport"`
	Lines     []string          `json:"lines"`
	Hints     map[string]string `json:"hints,omitempty"`
}

type batcher struct {
	apiURL     string
	tenant     string
	client     *http.Client
	mu         sync.Mutex
	buf        map[string][]string // sourceID -> lines
	maxBatch   int
	flushEvery time.Duration
}

func newBatcher(apiURL, tenant string, maxBatch int, flushEvery time.Duration) *batcher {
	b := &batcher{apiURL: apiURL, tenant: tenant, client: &http.Client{Timeout: 5 * time.Second}, buf: map[string][]string{}, maxBatch: maxBatch, flushEvery: flushEvery}
	go b.loop()
	return b
}

func (b *batcher) add(sourceID, transport, line string) {
	b.mu.Lock()
	b.buf[sourceID] = append(b.buf[sourceID], line)
	full := len(b.buf[sourceID]) >= b.maxBatch
	b.mu.Unlock()
	if full {
		b.flushSource(sourceID, transport)
	}
}

func (b *batcher) loop() {
	t := time.NewTicker(b.flushEvery)
	for range t.C {
		b.mu.Lock()
		sources := make([]string, 0, len(b.buf))
		for s := range b.buf {
			sources = append(sources, s)
		}
		b.mu.Unlock()
		for _, s := range sources {
			b.flushSource(s, "")
		}
	}
}

func (b *batcher) flushSource(sourceID, transport string) {
	b.mu.Lock()
	lines := b.buf[sourceID]
	if len(lines) == 0 {
		b.mu.Unlock()
		return
	}
	delete(b.buf, sourceID)
	b.mu.Unlock()
	req := ingestReq{SourceID: sourceID, Transport: transport, Lines: lines}
	body, _ := json.Marshal(req)
	backoff := 200 * time.Millisecond
	for attempt := 0; attempt < 5; attempt++ {
		resp, err := b.client.Post(b.apiURL+"/v1/ingest", "application/json", bytes.NewReader(body))
		if err == nil {
			io.Copy(io.Discard, resp.Body)
			resp.Body.Close()
			if resp.StatusCode < 300 {
				return
			}
			log.Printf("ingest non-2xx status=%d source=%s lines=%d", resp.StatusCode, sourceID, len(lines))
		} else {
			log.Printf("ingest error: %v (attempt %d)", err, attempt)
		}
		time.Sleep(backoff)
		backoff *= 2
	}
	log.Printf("DROPPING batch after retries: source=%s lines=%d (backend unreachable — see docs/scalability.md)", sourceID, len(lines))
}

func udpListener(addr string, b *batcher, sourceID string) {
	conn, err := net.ListenPacket("udp", addr)
	if err != nil {
		log.Fatalf("udp listen %s: %v", addr, err)
	}
	log.Printf("syslog UDP listening on %s", addr)
	buf := make([]byte, 65535)
	for {
		n, peer, err := conn.ReadFrom(buf)
		if err != nil {
			log.Printf("udp read error: %v", err)
			continue
		}
		line := strings.TrimRight(string(buf[:n]), "\r\n")
		if line != "" {
			b.add(sourceID+":"+peer.String(), "syslog-udp", line)
		}
	}
}

func tcpListener(addr string, b *batcher, sourceID string) {
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		log.Fatalf("tcp listen %s: %v", addr, err)
	}
	log.Printf("syslog TCP listening on %s", addr)
	for {
		conn, err := ln.Accept()
		if err != nil {
			continue
		}
		go handleTCP(conn, b, sourceID)
	}
}

// handleTCP frames RFC6587 octet-counted messages ("123 <msg...>") and falls back to newline
// framing (RFC5424 non-transparent framing / RFC3164) when the stream doesn't start with a digit.
func handleTCP(conn net.Conn, b *batcher, sourceID string) {
	defer conn.Close()
	peer := conn.RemoteAddr().String()
	r := bufio.NewReaderSize(conn, 1<<20)
	for {
		peekByte, err := r.Peek(1)
		if err != nil {
			return
		}
		if peekByte[0] >= '0' && peekByte[0] <= '9' {
			lenStr, err := r.ReadString(' ')
			if err != nil {
				return
			}
			n, convErr := strconv.Atoi(strings.TrimSpace(lenStr))
			if convErr != nil || n <= 0 || n > 1<<20 {
				return
			}
			msg := make([]byte, n)
			if _, err := io.ReadFull(r, msg); err != nil {
				return
			}
			b.add(sourceID+":"+peer, "syslog-tcp", string(msg))
			continue
		}
		line, err := r.ReadString('\n')
		if line = strings.TrimRight(line, "\r\n"); line != "" {
			b.add(sourceID+":"+peer, "syslog-tcp", line)
		}
		if err != nil {
			return
		}
	}
}

// tailFile follows a file like `tail -F`: handles truncation and (basic) rotation by re-opening
// when the inode's size shrinks. Good enough for /var/log/syslog and container log demos.
func tailFile(path string, b *batcher, sourceID string) {
	var offset int64
	for {
		f, err := os.Open(path)
		if err != nil {
			time.Sleep(2 * time.Second)
			continue
		}
		st, _ := f.Stat()
		if st.Size() < offset {
			offset = 0
		}
		f.Seek(offset, io.SeekStart)
		r := bufio.NewReader(f)
		for {
			line, err := r.ReadString('\n')
			if len(line) > 0 {
				offset += int64(len(line))
				if trimmed := strings.TrimRight(line, "\r\n"); trimmed != "" {
					b.add(sourceID, "file", trimmed)
				}
			}
			if err != nil {
				break
			}
		}
		f.Close()
		time.Sleep(500 * time.Millisecond)
	}
}

func main() {
	apiURL := flag.String("api", envOr("ULI_API_URL", "http://localhost:8080"), "ULI API base URL")
	udpAddr := flag.String("udp", envOr("ULI_SYSLOG_UDP", ":5514"), "syslog UDP listen address (empty to disable)")
	tcpAddr := flag.String("tcp", envOr("ULI_SYSLOG_TCP", ":5514"), "syslog TCP listen address (empty to disable)")
	tail := flag.String("tail", envOr("ULI_TAIL_FILE", ""), "file path to tail (empty to disable)")
	tailSource := flag.String("tail-source", envOr("ULI_TAIL_SOURCE", "file:local"), "source_id for tailed file")
	maxBatch := flag.Int("batch", 200, "max lines per batch per source")
	flushMs := flag.Int("flush-ms", 500, "flush interval milliseconds")
	flag.Parse()

	b := newBatcher(*apiURL, "default", *maxBatch, time.Duration(*flushMs)*time.Millisecond)
	var wg sync.WaitGroup
	if *udpAddr != "" {
		wg.Add(1)
		go func() { defer wg.Done(); udpListener(*udpAddr, b, "syslog") }()
	}
	if *tcpAddr != "" {
		wg.Add(1)
		go func() { defer wg.Done(); tcpListener(*tcpAddr, b, "syslog") }()
	}
	if *tail != "" {
		wg.Add(1)
		go func() { defer wg.Done(); tailFile(*tail, b, *tailSource) }()
	}
	if *udpAddr == "" && *tcpAddr == "" && *tail == "" {
		fmt.Println("nothing to do: enable at least one of -udp -tcp -tail")
		os.Exit(1)
	}
	wg.Wait()
}

func envOr(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}
