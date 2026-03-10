import argparse
import json
import warnings
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any

# tkinter backend emits spurious warnings when a glyph is missing from the
# chosen font; ignore any such UserWarning messages so the console stays clean.
warnings.filterwarnings("ignore", category=UserWarning, message=".*Glyph.*missing.*font.*")



FIELD_LABELS = {
    "col": "弹幕颜色",
    "level": "用户等级",
    "bl": "勋章等级(bl)",
    "fl": "粉丝等级(fl)",
    "pdg": "礼物亲密度(pdg)",
    "pdk": "弹幕活跃度(pdk)",
    "dms": "弹幕样式(dms)",
}


COLOR_VALUE_MAP = {
    "0": ("白字", "#C8C8C8"),
    "1": ("红字", "#FF4D4F"),
    "2": ("蓝字", "#4096FF"),
    "3": ("绿字", "#52C41A"),
    "4": ("橙字", "#FA8C16"),
    "5": ("紫字", "#9254DE"),
    "6": ("粉字", "#EB2F96"),
    "7": ("金字", "#FAAD14"),
    "8": ("青字", "#13C2C2"),
}


def _parse_cst_to_datetime(cst_raw: Any) -> datetime | None:
    value = _to_number(cst_raw)
    if value is None:
        return None

    # Mixed logs may contain seconds and milliseconds; detect by magnitude.
    if value >= 1e11:
        ts = value / 1000.0
    else:
        ts = value

    try:
        dt = datetime.fromtimestamp(ts)
    except (OverflowError, OSError, ValueError):
        return None

    # Guard abnormal timestamps to avoid broken x-axis ranges.
    if dt.year < 2000 or dt.year > 2100:
        return None
    return dt


def _format_color_mode(mode_code: str) -> tuple[str, str | None]:
    code = str(mode_code).strip()
    if not code:
        code = "0"
    name, hex_color = COLOR_VALUE_MAP.get(code, (f"未知色({code})", None))
    if hex_color:
        return f"{name} {hex_color}", hex_color
    return name, None


def _build_minute_series(minute_counts: dict[str, int]) -> tuple[list[datetime], list[int]]:
    if not minute_counts:
        return [], []

    points = sorted(datetime.strptime(k, "%Y-%m-%d %H:%M") for k in minute_counts)
    start = points[0]
    end = points[-1]

    x: list[datetime] = []
    y: list[int] = []
    current = start
    while current <= end:
        key = current.strftime("%Y-%m-%d %H:%M")
        x.append(current)
        y.append(minute_counts.get(key, 0))
        current += timedelta(minutes=1)
    return x, y


def _moving_average(values: list[int], window: int) -> list[float]:
    if not values:
        return []
    if window <= 1:
        return [float(v) for v in values]

    result: list[float] = []
    window_sum = 0.0
    queue: list[float] = []
    for v in values:
        value = float(v)
        queue.append(value)
        window_sum += value
        if len(queue) > window:
            window_sum -= queue.pop(0)
        result.append(window_sum / len(queue))
    return result


def _to_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def iter_log_files(folder: Path, recursive: bool) -> list[Path]:
    pattern = "**/*.log" if recursive else "*.log"
    files = sorted(folder.glob(pattern))
    return [f for f in files if f.is_file()]


def _wrap_text(text: str, width: int = 12) -> str:
    """Insert line breaks roughly every ``width`` characters.

    Chinese text has no spaces, so we just break on character count.  For
    longer English words or emoji sequences this may split mid‑glyph, but
    it keeps table cells from growing too wide.
    """
    if not text:
        return text
    text = str(text)
    if len(text) <= width:
        return text
    parts = [text[i : i + width] for i in range(0, len(text), width)]
    return "\n".join(parts)


def parse_records(files: list[Path]) -> tuple[dict[str, int], list[dict[str, Any]], int, int]:
    minute_counts: dict[str, int] = defaultdict(int)
    records: list[dict[str, Any]] = []
    total_lines = 0
    bad_lines = 0

    for log_file in files:
        with log_file.open("r", encoding="utf-8", errors="ignore") as f:
            for raw in f:
                total_lines += 1
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue

                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    bad_lines += 1
                    continue

                if not isinstance(data, dict):
                    bad_lines += 1
                    continue

                dt = _parse_cst_to_datetime(data.get("cst"))
                if dt is None:
                    bad_lines += 1
                    continue

                # retain parsed datetime for later window computation
                data["cst_dt"] = dt

                minute_key = dt.strftime("%Y-%m-%d %H:%M")
                minute_counts[minute_key] += 1
                records.append(data)

    return dict(minute_counts), records, total_lines, bad_lines


def field_stats(records: list[dict[str, Any]], fields: list[str]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []

    for field in fields:
        numeric_values: list[float] = []
        raw_values: list[str] = []
        modes: list[str] = []
        max_freq = 0

        for record in records:
            raw = record.get(field)
            if raw is None:
                continue
            raw_text = str(raw).strip()
            if not raw_text:
                continue

            raw_values.append(raw_text)
            number = _to_number(raw)
            if number is not None:
                numeric_values.append(number)

        if raw_values:
            counter = Counter(raw_values)
            max_freq = max(counter.values())
            modes = [k for k, v in counter.items() if v == max_freq]
            preview = modes[:3]
            mode_text = ",".join(preview)
            if len(modes) > 3:
                mode_text += f" ...(+{len(modes) - 3})"
            non_empty_count = len(raw_values)
        else:
            mode_text = ""
            non_empty_count = 0

        if numeric_values:
            avg_val = mean(numeric_values)
            med_val = median(numeric_values)
            avg_text = f"{avg_val:.4f}"
            med_text = f"{med_val:.4f}"
        else:
            avg_text = ""
            med_text = ""

        output.append(
            {
                "field": field,
                "field_zh": FIELD_LABELS.get(field, field),
                "non_empty": non_empty_count,
                "mean": avg_text,
                "median": med_text,
                "mode": mode_text,
                "mode_raw": modes[0] if raw_values else "",
                "mode_count": max_freq if raw_values else 0,
                "mode_display": mode_text,
                "mode_color": None,
            }
        )

    for row in output:
        if row["field"] == "col":
            mode_display, mode_color = _format_color_mode(row["mode_raw"])
            row["mode_display"] = mode_display
            row["mode_color"] = mode_color
            row["mean"] = "-"
            row["median"] = "-"
        elif not row["mode_display"]:
            row["mode_display"] = "-"

    return output


def compute_peaks(
    records: list[dict[str, Any]],
    minute_counts: dict[str, int],
) -> dict[int, dict[str, list[dict[str, Any]]]]:
    """Build specialized peak statistics for three- and ten-minute windows.

    - 3-minute windows: return up to *ten* highest-count intervals that
      **do not overlap** with each other.
    - 10-minute windows: return two lists, one of the top five highest-
      count intervals (may overlap) and another of five non-overlapping
      peak intervals (greedily chosen by descending count).

    The return value is a dict keyed by window length (3 and 10), where
    each value is another dict containing the appropriate lists:
    ``{"nonoverlap": [...]} `` for 3, and
    ``{"max": [...], "nonoverlap": [...]} `` for 10.  Each entry in the
    inner lists is a dict with ``start`` (string), ``count`` and ``top``
    (most frequent danmaku text).
    """
    times, counts = _build_minute_series(minute_counts)
    peaks: dict[int, dict[str, list[dict[str, Any]]]] = {3: {"nonoverlap": []}, 10: {"max": [], "nonoverlap": []}}

    if not times:
        return peaks

    def _most_common_text(start_dt: datetime, end_dt: datetime) -> str:
        texts: list[str] = []
        for rec in records:
            dt = rec.get("cst_dt")
            if not isinstance(dt, datetime):
                continue
            if start_dt <= dt < end_dt:
                txt = rec.get("txt")
                if txt is not None:
                    texts.append(str(txt))
        if not texts:
            return ""
        return Counter(texts).most_common(1)[0][0]

    def _sliding_candidates(window: int) -> list[tuple[int, datetime]]:
        n = len(times)
        cand: list[tuple[int, datetime]] = []
        j = 0
        for i in range(n):
            start = times[i]
            # advance j for current window
            while j < n and times[j] < start + timedelta(minutes=window):
                j += 1
            total = sum(counts[i:j])
            cand.append((total, start))
        return cand

    # compute 3-minute non-overlapping top 10
    cand3 = sorted(_sliding_candidates(3), key=lambda t: t[0], reverse=True)
    selected: list[tuple[int, datetime]] = []
    for total, start in cand3:
        if len(selected) >= 10:
            break
        end = start + timedelta(minutes=3)
        if any(not (end <= s or start >= (s + timedelta(minutes=3))) for _, s in selected):
            continue
        selected.append((total, start))
    for total, start in selected:
        peaks[3]["nonoverlap"].append({
            "start": start.strftime("%Y-%m-%d %H:%M"),
            "count": total,
            "top": _most_common_text(start, start + timedelta(minutes=3)),
        })

    # compute 10-minute candidates in chronological order so we can detect local extrema
    cand10_chrono = _sliding_candidates(10)
    # find local maxima (value greater than previous and next)
    extrema: list[tuple[int, datetime]] = []
    for idx in range(1, len(cand10_chrono) - 1):
        prev_val, prev_start = cand10_chrono[idx - 1]
        val, start = cand10_chrono[idx]
        next_val, _ = cand10_chrono[idx + 1]
        if val > prev_val and val > next_val:
            extrema.append((val, start))
    # sort by count descending and take top five
    for total, start in sorted(extrema, key=lambda t: t[0], reverse=True)[:5]:
        peaks[10]["max"].append({
            "start": start.strftime("%Y-%m-%d %H:%M"),
            "count": total,
            "top": _most_common_text(start, start + timedelta(minutes=10)),
        })
    # greedy non-overlapping selection up to five using descending counts
    cand10 = sorted(cand10_chrono, key=lambda t: t[0], reverse=True)
    selected = []
    for total, start in cand10:
        if len(selected) >= 5:
            break
        end = start + timedelta(minutes=10)
        if any(not (end <= s or start >= (s + timedelta(minutes=10))) for _, s in selected):
            continue
        selected.append((total, start))
    for total, start in selected:
        peaks[10]["nonoverlap"].append({
            "start": start.strftime("%Y-%m-%d %H:%M"),
            "count": total,
            "top": _most_common_text(start, start + timedelta(minutes=10)),
        })

    return peaks


def show_dashboard(
    minute_counts: dict[str, int],
    stats: list[dict[str, Any]],
    total_lines: int,
    bad_lines: int,
    peaks: dict[int, dict[str, list[dict[str, Any]]]],
    smooth_window: int,
) -> bool:
    try:
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        print("[WARN] matplotlib not installed. Cannot show dashboard.")
        print("       Install with: pip install matplotlib")
        return False

    plt.rcParams["font.sans-serif"] = [
        "Microsoft YaHei",
        "SimHei",
        "Noto Sans CJK SC",
        "Source Han Sans SC",
        "PingFang SC",
        "WenQuanYi Zen Hei",
        "Arial Unicode MS",
        "DejaVu Sans",
        "Segoe UI Emoji",  # fallback for emojis
        "Noto Color Emoji",  # another common emoji font
    ]
    plt.rcParams["axes.unicode_minus"] = False

    import matplotlib.gridspec as gridspec

    fig = plt.figure(figsize=(14, 10))
    gs = gridspec.GridSpec(2, 2, width_ratios=[3, 2], height_ratios=[2, 3], figure=fig)
    ax_curve = fig.add_subplot(gs[0, 0])      # top-left
    ax_table = fig.add_subplot(gs[1, 0])      # bottom-left
    ax_peak = fig.add_subplot(gs[:, 1])       # right column spanning both rows

    if minute_counts:
        x, y = _build_minute_series(minute_counts)
        smooth = _moving_average(y, smooth_window)
        x_num = mdates.date2num(x)
        # 主轴使用北京时间
        ax_curve.xaxis.set_major_locator(mdates.AutoDateLocator())
        ax_curve.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M", tz=timezone(timedelta(hours=8))))
        # 底部增加格林尼治时间刻度
        sec = ax_curve.secondary_xaxis('bottom', functions=(lambda x:x, lambda x:x))
        sec.xaxis.set_major_locator(mdates.AutoDateLocator())
        sec.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M", tz=timezone.utc))

        ax_curve.plot(x_num, y, linewidth=1.0, alpha=0.35, label="每分钟原始值")
        ax_curve.plot(x_num, smooth, linewidth=2.2, color="#d9480f", label=f"{smooth_window}分钟平滑")
        ax_curve.set_title("每分钟弹幕数量")
        ax_curve.set_xlabel("时间")
        ax_curve.set_ylabel("弹幕数")
        ax_curve.grid(alpha=0.3)
        ax_curve.legend(loc="upper right")
        fig.autofmt_xdate()
    else:
        ax_curve.text(0.5, 0.5, "没有可用弹幕记录", ha="center", va="center")
        ax_curve.set_axis_off()

    ax_table.set_axis_off()
    # 峰值表格
    ax_peak.set_axis_off()
    peak_headers=["类别","窗口(分钟)","开始时间","弹幕数","最常弹幕"]
    peak_rows=[]
    # 3-minute non-overlap
    for it in peaks.get(3, {}).get("nonoverlap", []):
        peak_rows.append([
            "3分钟非重叠",
            "3",
            it['start'],
            str(it['count']),
            _wrap_text(it['top']),
        ])
    # 10-minute extrema
    for it in peaks.get(10, {}).get("max", []):
        peak_rows.append([
            "10分钟极大值",
            "10",
            it['start'],
            str(it['count']),
            _wrap_text(it['top']),
        ])
    # 10-minute non-overlap
    for it in peaks.get(10, {}).get("nonoverlap", []):
        peak_rows.append([
            "10分钟非重叠",
            "10",
            it['start'],
            str(it['count']),
            _wrap_text(it['top']),
        ])
    peak_tbl = ax_peak.table(cellText=peak_rows,colLabels=peak_headers,loc="center",cellLoc="center")
    peak_tbl.auto_set_font_size(False)
    peak_tbl.set_fontsize(10)
    peak_tbl.scale(1,1.2)
    headers = ["字段", "非空数量", "平均数", "中位数", "众数"]
    rows = [
        [
            row["field_zh"],
            str(row["non_empty"]),
            row["mean"] or "-",
            row["median"] or "-",
            row["mode_display"] or "-",
        ]
        for row in stats
    ]

    table = ax_table.table(
        cellText=rows,
        colLabels=headers,
        colLoc="center",
        cellLoc="center",
        loc="center",
        colWidths=[0.25, 0.14, 0.14, 0.14, 0.33],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(11)
    table.scale(1, 1.4)

    for idx, row in enumerate(stats, start=1):
        if row.get("field") == "col" and row.get("mode_color"):
            table[(idx, 4)].set_facecolor(row["mode_color"])
            table[(idx, 4)].set_text_props(color="white")

    fig.suptitle(
        f"弹幕统计看板 | 总行数={total_lines} 已解析={sum(minute_counts.values())} 异常行={bad_lines}",
        fontsize=12,
    )
    plt.tight_layout(rect=(0, 0, 1, 0.97))
    plt.show()
    return True


def print_summary(minute_counts: dict[str, int], stats: list[dict[str, Any]], total_lines: int, bad_lines: int, peaks: dict[int, dict[str, list[dict[str,Any]]]]) -> None:
    print("\n=== Parse Summary ===")
    print(f"Total lines: {total_lines}")
    print(f"Parsed records: {sum(minute_counts.values())}")
    print(f"Skipped/bad lines: {bad_lines}")
    print(f"Minutes covered: {len(minute_counts)}")

    if minute_counts:
        counter = Counter(minute_counts)
        top_items = counter.most_common(5)
        print("Top 5 minute counts:")
        for minute, count in top_items:
            print(f"  {minute}: {count}")

    print("\n=== Field Stats ===")
    for row in stats:
        print(
            f"{row['field_zh']}: non_empty={row['non_empty']} "
            f"mean={row['mean'] or '-'} median={row['median'] or '-'} mode={row['mode_display'] or '-'}"
        )
    # 输出峰值
    print("\n=== 窗口峰值统计 ===")
    # 3-minute non-overlapping top 10
    m3 = peaks.get(3, {}).get("nonoverlap", [])
    if m3:
        print("-- 3分钟非重叠窗口 Top10 --")
        for it in m3:
            print(f"  {it['start']} count={it['count']} top_danmaku={it['top']}")
    # 10-minute extrema and peaks
    m10max = peaks.get(10, {}).get("max", [])
    if m10max:
        print("-- 10分钟极大值(Top5) --")
        for it in m10max:
            print(f"  {it['start']} count={it['count']} top_danmaku={it['top']}")
    m10no = peaks.get(10, {}).get("nonoverlap", [])
    if m10no:
        print("-- 10分钟非重叠五个窗口 --")
        for it in m10no:
            print(f"  {it['start']} count={it['count']} top_danmaku={it['top']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze danmaku logs in a folder.")
    parser.add_argument("folder", help="Target folder containing .log files")
    parser.add_argument(
        "--fields",
        default="col,level,bl,fl,pdg,pdk",
        help="Comma-separated fields for mean/median/mode stats (default excludes 弹幕样式)",
    )
    parser.add_argument("--recursive", action="store_true", help="Scan .log recursively")
    parser.add_argument("--smooth-window", type=int, default=10, help="Smooth window in minutes")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    folder = Path(args.folder)
    if not folder.exists() or not folder.is_dir():
        raise SystemExit(f"Target folder not found: {folder}")

    fields = [f.strip() for f in args.fields.split(",") if f.strip()]
    files = iter_log_files(folder, recursive=args.recursive)
    if not files:
        raise SystemExit(f"No .log files found in: {folder}")

    minute_counts, records, total_lines, bad_lines = parse_records(files)
    stats = field_stats(records, fields)
    peaks = compute_peaks(records, minute_counts)

    print_summary(minute_counts, stats, total_lines, bad_lines, peaks)
    show_dashboard(minute_counts, stats, total_lines, bad_lines, peaks, smooth_window=max(1, args.smooth_window))


if __name__ == "__main__":
    main()
