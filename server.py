#!/usr/bin/env python3
"""Server: serves dashboard.html and resolves trade outcomes server-side."""

import http.server, urllib.request, urllib.parse, json, os, warnings
from datetime import datetime, timezone, timedelta
from collections import defaultdict

warnings.filterwarnings("ignore")
import yfinance as yf

ALERTS_URL = (
    "https://trading-view-automation-production.up.railway.app"
    "/webhook/c7291bbc-9c95-480f-94f5-cad368ffa41e/alert"
)

TV_TO_YF = {
    'GC1!': 'GC=F', 'SI1!': 'SI=F', 'HG1!': 'HG=F', 'PL1!': 'PL=F',
    'CL1!': 'CL=F', 'NG1!': 'NG=F', 'RB1!': 'RB=F', 'HO1!': 'HO=F',
    'NQ1!': 'NQ=F', 'ES1!': 'ES=F', 'YM1!': 'YM=F', 'RTY1!': 'RTY=F',
    'ZN1!': 'ZN=F', 'ZB1!': 'ZB=F', 'ZC1!': 'ZC=F', 'ZS1!': 'ZS=F',
    'ZW1!': 'ZW=F',
}

TV_INTERVAL_TO_YF = {
    '1': '1m', '2': '2m', '5': '5m', '15': '15m',
    '30': '30m', '60': '60m', '240': '1h', 'D': '1d', 'W': '1wk',
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}


def parse_content(raw):
    obj = {}
    for line in (raw or '').split('\n'):
        idx = line.find(' - ')
        if idx == -1:
            continue
        k, v = line[:idx].strip(), line[idx + 3:].strip()
        obj[k] = None if v == 'null' else v
    return obj


def fetch_alerts():
    req = urllib.request.Request(ALERTS_URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def resolve_outcomes(signals):
    now = datetime.now(timezone.utc)
    end_str = (now + timedelta(days=1)).strftime('%Y-%m-%d')

    groups = defaultdict(list)
    for s in signals:
        if s['entry'] and s['tp'] and s['sl']:
            groups[s['ticker']].append(s)

    for ticker, sigs in groups.items():
        yf_sym    = TV_TO_YF.get(ticker, ticker.replace('!', ''))
        yf_ivl    = TV_INTERVAL_TO_YF.get(sigs[0]['interval'], '1d')
        min_time  = min(datetime.fromisoformat(s['time'].replace('Z', '+00:00')) for s in sigs)
        start_str = min_time.strftime('%Y-%m-%d')

        print(f"[price] {yf_sym} {yf_ivl}  {start_str} → {end_str}", flush=True)
        try:
            df = yf.Ticker(yf_sym).history(
                start=start_str, end=end_str, interval=yf_ivl, auto_adjust=True
            )
        except Exception as e:
            print(f"[price error] {yf_sym}: {e}", flush=True)
            continue

        if df.empty:
            print(f"[price] {yf_sym}: no data", flush=True)
            continue

        print(f"[price] {yf_sym}: {len(df)} bars", flush=True)

        for s in sigs:
            entry_ts = int(datetime.fromisoformat(s['time'].replace('Z', '+00:00')).timestamp())
            tp, sl   = float(s['tp']), float(s['sl'])
            is_long  = s['side'] == 'long'

            for ts, row in df.iterrows():
                if int(ts.timestamp()) < entry_ts:
                    continue
                tp_hit = row['Low']  <= tp if not is_long else row['High'] >= tp
                sl_hit = row['High'] >= sl if not is_long else row['Low']  <= sl
                if tp_hit and sl_hit:
                    s['outcome'] = 'ambiguous'
                elif tp_hit:
                    s['outcome'] = 'win'
                elif sl_hit:
                    s['outcome'] = 'loss'
                if s['outcome'] != 'open':
                    break

    return signals


def build_outcomes():
    raw     = fetch_alerts()
    signals = []

    for item in raw:
        c    = parse_content(item.get('content', ''))
        buy  = c.get('Buy Signal',  '0') == '1'
        sell = c.get('Sell Signal', '0') == '1'
        if not buy and not sell:
            continue

        entry = c.get('Long Entry' if buy else 'Short Entry')
        tp    = c.get('Long TP'    if buy else 'Short TP')
        sl    = c.get('Long SL'    if buy else 'Short SL')

        signals.append({
            'ticker':   c.get('Ticker', '?'),
            'exchange': c.get('Exchange', '?'),
            'interval': c.get('Interval', '?'),
            'time':     c.get('Time'),
            'side':     'long' if buy else 'short',
            'entry':    entry,
            'tp':       tp,
            'sl':       sl,
            'outcome':  'open',
        })

    return resolve_outcomes(signals)


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == '/api/outcomes':
            try:
                data = build_outcomes()
                self._ok(json.dumps(data).encode())
            except Exception as e:
                print(f"[error] {e}", flush=True)
                self._err(e)
        else:
            super().do_GET()

    def _ok(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(data)

    def _err(self, e):
        self.send_response(502)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps({'error': str(e)}).encode())

    def log_message(self, fmt, *args):
        pass


os.chdir(os.path.dirname(os.path.abspath(__file__)))
PORT = int(os.environ.get('PORT', 8742))
print(f"Dashboard → http://localhost:{PORT}/dashboard.html", flush=True)
http.server.HTTPServer(('', PORT), Handler).serve_forever()
