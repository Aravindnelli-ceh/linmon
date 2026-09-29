# linmon

Lightweight realtime CPU and memory monitor for Linux. It reads `/proc` directly, needs no dependencies, and serves a live-graph dashboard in your browser.

## Features
- Live CPU usage graph with per-core bars, load average and peak
- Live memory graph with swap, cache and used/total figures
- Keeps the last 2 minutes of history, so a page refresh doesn't reset the graphs
- Single Python file, standard library only

## Requirements
- Linux (uses `/proc/stat` and `/proc/meminfo`)
- Python 3

## Usage
```bash
python3 linmon.py
```
Then open http://127.0.0.1:8080

### Options
| Flag | Default | Description |
|------|---------|-------------|
| `--host` | `127.0.0.1` | Bind address (`0.0.0.0` to expose on your network) |
| `--port` | `8080` | Port to listen on |
| `--interval` | `1.0` | Seconds between samples |

> **Note:** There is no authentication. Only use `--host 0.0.0.0` on a trusted network.

## How it works
A background thread samples `/proc/stat` and `/proc/meminfo` at a fixed interval and computes usage from the deltas. A small built-in HTTP server exposes the data as JSON, and the page draws the graphs on a canvas.

## License
MIT
