# Sample log datasets

Twelve real (not synthetic) datasets, used for development, testing, and the demo script — per the
project requirement to validate against real log data rather than hand-crafted fixtures. Each
directory was produced by `scripts/fetch_logs.py`, which downloads the source archive, verifies its
checksum (where the source publishes one), extracts a deterministic sample, and writes a
`LICENSE-NOTE.txt` alongside it recording provenance, license terms, and the exact sample-vs-total
line counts — reproducible via `make fetch-logs` (`python scripts/fetch_logs.py [--only name1,name2]`).

Every sample here is 2,000 lines (`fetch_logs.py --sample-lines 2000`, the default), taken from the
head of the source file; `LICENSE-NOTE.txt` in each directory states the true total line count.

| Directory | Vendor / system | Format | Source | Total lines available |
|---|---|---|---|---|
| `linux/` | Linux (syslog) | BSD syslog | Loghub (Zenodo 3227177) | 25,567 |
| `apache/` | Apache httpd | Apache error log | Loghub | 56,482 |
| `ssh/` | OpenSSH (`sshd`) | BSD syslog | Loghub | 655,147 |
| `zookeeper/` | Apache ZooKeeper | log4j | Loghub | 74,380 |
| `mac/` | macOS | BSD syslog | Loghub | 117,283 |
| `healthapp/` | HealthApp (Android) | pipe-delimited app log | Loghub | 253,395 |
| `proxifier/` | Proxifier | proxy client log | Loghub | 21,329 |
| `hpc/` | HPC cluster | space-delimited | Loghub | 433,490 |
| `hadoop/` | Apache Hadoop | log4j | Loghub | 394,310 |
| `squid/` | Squid proxy | Squid native access log | SecRepo | 1,643,520 |
| `zeek_conn/` | Zeek/Bro | Zeek TSV `conn.log` | SecRepo | 22,694,356 |
| `auth/` | Linux `auth.log` | BSD syslog | SecRepo | 86,839 |

## Licenses

- **Loghub** (logpai) datasets: free for research/academic use with citation — Jieming Zhu et al.,
  *"Loghub: A Large Collection of System Log Datasets for AI-driven Log Analytics"*, ISSRE 2023.
  The citation requirement is carried into every `LICENSE-NOTE.txt` under a Loghub-sourced
  directory; redistribute copies with that notice attached.
- **SecRepo** (Mike Sconzo, secrepo.com) datasets: see the site for per-file terms; samples here
  are redistributed for research/demo purposes with attribution.

Neither license permits presenting these logs as originating from this project's own
infrastructure — they are third-party research/security datasets used to validate that the parsing
ladder handles real-world format diversity (syslog, log4j, pipe-delimited, Zeek TSV, Squid native)
rather than only the hand-written fixtures in `tests/`.

## Reproducing / extending the sample set

```
python scripts/fetch_logs.py                 # fetch + sample all 12
python scripts/fetch_logs.py --only ssh,auth  # just these two
python scripts/fetch_logs.py --offline        # rebuild samples from already-downloaded archives
```

Full archives land in `logs/raw-datasets/` (git-ignored) so re-sampling with a different
`--sample-lines` value doesn't require re-downloading. `fetch_logs.py` verifies MD5 checksums for
every Loghub dataset before sampling; SecRepo sources are unauthenticated `.gz` downloads (no
published checksum to verify against).

## How these are used elsewhere in this project

- `scripts/demo.py` reads from here for the "known vendor, real traffic" demo scenario.
- Adversarial/integration tests use small hand-written fixtures instead of these (deterministic,
  minimal, easy to reason about in a failing-test diff) — these datasets are for volume/diversity
  validation and the demo, not unit-test fixtures.
