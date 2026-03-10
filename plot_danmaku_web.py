"""Generate an interactive HTML dashboard for danmaku counts.

This script reuses the parsing/statistics logic from
``plot_danmaku_stats.py`` but produces a standalone HTML page using
Plotly.  Hovering over a point on the main curve will display the
"hottest" (most frequent) danmaku text seen in that minute.  The page
can be opened directly in a browser.

Usage:
    python plot_danmaku_web.py <log_folder>
    (non-recursive; only .log files immediately under the folder are used)

"""

from pathlib import Path
from collections import Counter
import argparse

# import functions from existing analysis script
from plot_danmaku_stats import (
    parse_records,
    _build_minute_series,
    _moving_average,
)

# server imports (optional, may not have flask installed)
try:
    from flask import Flask, render_template_string, request
except ImportError:
    Flask = None
    render_template_string = None
    request = None

try:
    import plotly.graph_objs as go
    import plotly.offline as pyo
except ImportError:
    raise SystemExit("Plotly is required; install with: pip install plotly")


def make_html(folder: Path, smooth_window: int = 10) -> Path:
    files = sorted(folder.glob("*.log"))  # non-recursive scan
    files = [f for f in files if f.is_file()]
    if not files:
        raise SystemExit(f"no .log files found in {folder}")

    minute_counts, records, total_lines, bad_lines = parse_records(files)

    x, y = _build_minute_series(minute_counts)
    smooth = _moving_average(y, smooth_window)

    # build hottest danmaku per minute (or empty string)
    top_by_min = {}
    for rec in records:
        dt = rec.get("cst_dt")
        if not dt:
            continue
        key = dt.strftime("%Y-%m-%d %H:%M")
        txt = rec.get("txt")
        if txt:
            top_by_min.setdefault(key, []).append(str(txt))
    top_mode = {k: Counter(v).most_common(1)[0][0] for k, v in top_by_min.items()}

    # convert x datetimes to strings for JSON
    xstr = [dt.strftime("%Y-%m-%d %H:%M") for dt in x]
    hover_texts = [top_mode.get(t, "") for t in xstr]
    # also include previous-minute hot danmaku ("前置")
    prev_texts = []
    for i, t in enumerate(xstr):
        if i == 0:
            prev_texts.append("")
        else:
            prev_texts.append(top_mode.get(xstr[i - 1], ""))

    combined = [[cur, prev] for cur, prev in zip(hover_texts, prev_texts)]

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=xstr,
            y=y,
            mode="lines",
            name="每分钟",
            hovertemplate=(
                "%{x}<br>count=%{y}<br>热弹幕(current): %{customdata[0]}<br>"
                "热弹幕(prev): %{customdata[1]}"
            ),
            customdata=combined,
        )
    )
    if smooth:
        fig.add_trace(
            go.Scatter(
                x=xstr,
                y=smooth,
                mode="lines",
                name=f"{smooth_window} 分钟平滑",
                line=dict(color="firebrick", width=2),
            )
        )

    fig.update_layout(
        title="弹幕每分钟数量",
        xaxis_title="时间",
        yaxis_title="弹幕数",
        hovermode="x unified",
    )

    out = folder / "danmaku_dashboard.html"
    pyo.plot(fig, filename=str(out), auto_open=True)
    return out


# --- embedded server support ---
PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="zh">
<head>
    <meta charset="utf-8">
    <title>弹幕统计看板</title>
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <style>body { font-family: "Microsoft YaHei", sans-serif; }</style>
</head>
<body>
<h2>弹幕每分钟数量</h2>
<form method="post">
    <label>日志文件夹: <input name="folder" size="40" value="{{ current_folder }}"></label>
    <button type="submit">加载</button>
</form>
<div id="chart" style="width:90%;height:60vh;"></div>
<script>
const data = {{ data | safe }};
const layout = {
    xaxis: { title: '时间' },
    yaxis: { title: '弹幕数' },
    hovermode: 'x unified'
};
Plotly.newPlot('chart', data, layout);
</script>
</body>
</html>"""

if Flask is not None:
    app = Flask(__name__)

    def build_data(folder: Path, smooth_window: int = 10):
        files = sorted(folder.glob("*.log"))
        files = [f for f in files if f.is_file()]
        if not files:
            raise SystemExit(f"no .log files found in {folder}")
        minute_counts, records, total_lines, bad_lines = parse_records(files)
        x, y = _build_minute_series(minute_counts)
        smooth = _moving_average(y, smooth_window)
        top_by_min = {}
        for rec in records:
            dt = rec.get("cst_dt")
            if not dt:
                continue
            key = dt.strftime("%Y-%m-%d %H:%M")
            txt = rec.get("txt")
            if txt:
                top_by_min.setdefault(key, []).append(str(txt))
        top_mode = {k: Counter(v).most_common(1)[0][0] for k, v in top_by_min.items()}
        xstr = [dt.strftime("%Y-%m-%d %H:%M") for dt in x]
        hover_texts = [top_mode.get(t, "") for t in xstr]
        prev_texts = ["" if i == 0 else top_mode.get(xstr[i - 1], "") for i in range(len(xstr))]
        custom = [[cur, prev] for cur, prev in zip(hover_texts, prev_texts)]
        trace1 = {
            'x': xstr,
            'y': y,
            'mode': 'lines',
            'name': '每分钟',
            'hovertemplate': '%{x}<br>count=%{y}<br>热弹幕(current): %{customdata[0]}<br>热弹幕(prev): %{customdata[1]}',
            'customdata': custom,
        }
        data = [trace1]
        if smooth:
            data.append({
                'x': xstr,
                'y': smooth,
                'mode': 'lines',
                'name': f'{smooth_window} 分钟平滑',
                'line': {'color': 'firebrick', 'width': 2},
            })
        return data

    @app.route('/', methods=['GET','POST'])
    def index():
        if request.method == 'POST':
            newfolder = request.form.get('folder')
            if newfolder:
                path = Path(newfolder)
                if path.exists() and path.is_dir():
                    app.config['STAT_DATA'] = build_data(path, smooth_window=app.config.get('SMOOTH',10))
                    app.config['CURRENT_FOLDER'] = str(path)
        return render_template_string(PAGE_TEMPLATE, data=json.dumps(app.config['STAT_DATA']), current_folder=app.config.get('CURRENT_FOLDER',''))


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("folder", help="folder containing .log files (non-recursive)")
    p.add_argument("--smooth-window", type=int, default=10)
    p.add_argument("--serve", action="store_true", help="start Flask server instead of writing HTML")
    return p.parse_args()


def main():
    args = parse_args()
    folder = Path(args.folder)
    if not folder.exists() or not folder.is_dir():
        raise SystemExit(f"folder not found: {folder}")

    if args.serve:
        if Flask is None:
            raise SystemExit("Flask not available; install with `pip install flask` to use --serve")
        app.config['SMOOTH'] = args.smooth_window
        app.config['CURRENT_FOLDER'] = str(folder)
        app.config['STAT_DATA'] = build_data(folder, smooth_window=args.smooth_window)
        print('serving on http://127.0.0.1:5000/')
        app.run()
    else:
        make_html(folder, smooth_window=args.smooth_window)


if __name__ == "__main__":
    main()
