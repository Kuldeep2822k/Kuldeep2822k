from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


README_PATH = Path(__file__).resolve().parents[2] / "README.md"
START_MARKER = "<!--START_SECTION:waka-->"
END_MARKER = "<!--END_SECTION:waka-->"
WAKA_BASE = "https://wakatime.com/api/v1"
GITHUB_BASE = "https://api.github.com"
MAX_LANGUAGES = 8
MAX_EDITORS_OR_OS = 3
NAME_WIDTH = 16
PCT_WIDTH = 6
HOURS_WIDTH = 8
ZERO_CLOCK_RE = re.compile(r"0+(?::0+){1,2}")
ZERO_UNIT_VALUES_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours|m|min|mins|minute|minutes|s|sec|secs|second|seconds)\b"
)


def _auth_header(api_key: str) -> dict[str, str]:
    token = base64.b64encode(f"{api_key}:".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


def _fetch_json(url: str, headers: dict[str, str]) -> Any:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_waka_json(path: str, api_key: str, params: dict[str, str] | None = None) -> Any:
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    return _fetch_json(f"{WAKA_BASE}{path}{query}", _auth_header(api_key))


def fetch_github_json(path: str, token: str, params: dict[str, str] | None = None) -> Any:
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    return _fetch_json(f"{GITHUB_BASE}{path}{query}", headers)


def fetch_all_repos(token: str) -> list[dict[str, Any]]:
    page = 1
    repos: list[dict[str, Any]] = []
    while True:
        batch = fetch_github_json(
            "/user/repos",
            token,
            {"per_page": "100", "page": str(page), "type": "owner", "sort": "updated"},
        )
        if not batch:
            break
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return repos


def safe_text(stats: dict[str, Any], key: str, fallback: str) -> str:
    value = stats.get(key)
    if value in (None, ""):
        return fallback
    return str(value)


def percent_bar(percent: float) -> str:
    filled = max(0, min(10, int(percent / 10 + 0.5)))
    return "▰" * filled + "▱" * (10 - filled)


def two_col(left: str, right: str, width: int = 60) -> str:
    return f"{left:<{width}} | {right}" if right else left


def right_col(text: str, width: int = 60) -> str:
    return f"{'':<{width}} | {text}"


def fmt_hours(seconds: float) -> str:
    return f"{seconds / 3600:.2f} h"


def humanize_seconds(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    if total == 0:
        return "0 secs"
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} hr" if hours == 1 else f"{hours} hrs")
    if minutes:
        parts.append(f"{minutes} min" if minutes == 1 else f"{minutes} mins")
    if secs:
        parts.append(f"{secs} sec" if secs == 1 else f"{secs} secs")
    return " ".join(parts)


def is_zero_time_text(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {
        "",
        "0",
        "0 sec",
        "0 secs",
        "0 second",
        "0 seconds",
        "0 min",
        "0 mins",
        "0 minute",
        "0 minutes",
        "0 hr",
        "0 hrs",
        "0 hour",
        "0 hours",
    }:
        return True
    if ZERO_CLOCK_RE.fullmatch(lowered):
        return True
    unit_values = ZERO_UNIT_VALUES_RE.findall(lowered)
    if unit_values and all(float(part) == 0 for part in unit_values):
        return True
    return False


def format_metric_row(
    label: str,
    percent: float,
    seconds: float,
    *,
    label_width: int = NAME_WIDTH,
    prefix: str = " ",
    suffix: str = "   |",
) -> str:
    return (
        f"{prefix}{label:<{label_width}} {percent_bar(percent)}   {percent:>{PCT_WIDTH}.2f} %"
        f"{suffix} {fmt_hours(seconds):>{HOURS_WIDTH}}"
    )


def platform_note(name: str) -> str:
    lowered = name.lower()
    if lowered.startswith("vs"):
        return "Focus mode ready."
    if lowered.startswith("win"):
        return "Automation friendly."
    return ""


def derive_top_repo_language(repos: list[dict[str, Any]]) -> dict[str, Any]:
    """Estimate top language from repository primary-language frequency when WakaTime is empty."""
    language_counts: Counter[str] = Counter()
    for repo in repos:
        language = str(repo.get("language") or "").strip()
        if language and language.lower() != "none":
            language_counts[language] += 1
    if not language_counts:
        return {"name": "N/A", "percent": 0.0}
    top_name, top_count = language_counts.most_common(1)[0]
    total = sum(language_counts.values())
    percent = (top_count / total * 100) if total else 0.0
    return {"name": top_name, "percent": percent}


def build_metrics_block() -> str:
    waka_key = os.environ["WAKATIME_API_KEY"].strip()
    gh_token = os.environ["GH_TOKEN"].strip()
    tz_name = os.environ.get("DISPLAY_TIMEZONE", "UTC")
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = timezone.utc

    end_day = datetime.now(timezone.utc).date()
    start_day = end_day - timedelta(days=6)
    stats = fetch_waka_json("/users/current/stats/last_7_days", waka_key)
    summaries = fetch_waka_json(
        "/users/current/summaries",
        waka_key,
        {"start": start_day.isoformat(), "end": end_day.isoformat()},
    )

    repos = fetch_all_repos(gh_token)

    repos_total = len(repos)
    repos_public = sum(1 for repo in repos if not repo.get("private"))
    repos_private = repos_total - repos_public
    stars = sum(int(repo.get("stargazers_count", 0)) for repo in repos)

    repo_years = [int(repo["created_at"][:4]) for repo in repos if repo.get("created_at")]
    year_from = min(repo_years) if repo_years else datetime.now().year
    year_to = datetime.now().year

    langs = stats.get("languages") or []
    editors = stats.get("editors") or []
    oses = stats.get("operating_systems") or []

    top_lang = langs[0] if langs else derive_top_repo_language(repos)
    top_editor = editors[0] if editors else {"name": "N/A", "percent": 0.0}

    weekday_seconds: defaultdict[str, float] = defaultdict(float)
    for day_info in summaries.get("data", []):
        day_str = day_info.get("range", {}).get("date")
        seconds = float(day_info.get("grand_total", {}).get("total_seconds", 0) or 0)
        if not day_str:
            continue
        day_name = datetime.strptime(day_str, "%Y-%m-%d").strftime("%A")
        weekday_seconds[day_name] += seconds

    bucket_seconds: dict[str, float] = {"Morning": 0.0, "Daytime": 0.0, "Evening": 0.0, "Night": 0.0}
    activity_chunks = 0
    durations_available = False
    for idx in range(7):
        target_day = start_day + timedelta(days=idx)
        try:
            durations = fetch_waka_json("/users/current/durations", waka_key, {"date": target_day.isoformat()})
        except urllib.error.HTTPError as error:
            if 500 <= error.code < 600:
                raise RuntimeError(
                    f"Failed fetching WakaTime durations for {target_day.isoformat()} (HTTP {error.code}). "
                    "WakaTime may be temporarily unavailable; retry on the next run."
                ) from error
            response_body = error.read().decode("utf-8", errors="replace")
            print(
                f"Skipping durations for {target_day.isoformat()} "
                f"(HTTP {error.code}): {response_body[:200]}"
            )
            durations = {"data": []}
        for item in durations.get("data", []):
            sec = float(item.get("duration", 0) or 0)
            stamp = float(item.get("time", 0) or 0)
            if sec <= 0:
                continue
            local_dt = datetime.fromtimestamp(stamp, tz=timezone.utc).astimezone(tz)
            durations_available = True
            hour = local_dt.hour
            if 6 <= hour < 12:
                bucket_seconds["Morning"] += sec
            elif 12 <= hour < 18:
                bucket_seconds["Daytime"] += sec
            elif 18 <= hour < 24:
                bucket_seconds["Evening"] += sec
            else:
                bucket_seconds["Night"] += sec
            activity_chunks += 1

    week_total = float(stats.get("total_seconds", 0) or 0)
    if week_total <= 0:
        week_total = sum(weekday_seconds.values())
    daily_average_seconds = float(stats.get("daily_average", 0) or 0)
    if daily_average_seconds <= 0 and week_total > 0:
        daily_average_seconds = week_total / 7

    used_duration_fallback = False
    if sum(bucket_seconds.values()) <= 0 and week_total > 0:
        # Durations can be unavailable for some accounts/permissions; keep layout stable with a single fallback bucket.
        bucket_seconds["Night"] = week_total
        used_duration_fallback = True

    peak_time_name, peak_time_value = max(bucket_seconds.items(), key=lambda x: x[1])
    if weekday_seconds:
        peak_day_name, peak_day_value = max(weekday_seconds.items(), key=lambda x: x[1])
    else:
        peak_day_name, peak_day_value = "N/A", 0.0

    language_quotes = [
        "Automation buys thinking time.",
        "Readable code scales teams.",
        "Tests turn fear into speed.",
        "Small loops build momentum.",
        "Refactor early, ship confidently.",
    ]
    period_quotes = [
        "Deep focus zone.",
        "Review and polish.",
        "Debug and refine.",
        "Plan and warm up.",
    ]
    day_quotes = {
        "Monday": "Automation day",
        "Tuesday": "Learning day",
        "Wednesday": "Planning day",
        "Thursday": "Momentum day",
        "Friday": "Shipping day",
        "Saturday": "Refactor day",
        "Sunday": "Review day",
    }

    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    periods = [
        ("Morning", "06-12"),
        ("Daytime", "12-18"),
        ("Evening", "18-24"),
        ("Night", "00-06"),
    ]

    total_display = safe_text(stats, "human_readable_total", "")
    if is_zero_time_text(total_display):
        total_display = humanize_seconds(week_total)
    daily_display = safe_text(stats, "human_readable_daily_average", "")
    if is_zero_time_text(daily_display):
        daily_display = humanize_seconds(daily_average_seconds)

    lines: list[str] = []
    lines.append(two_col("0x3EF8 · Dev Metrics", "Quick Insights"))
    lines.append(
        two_col(
            f"From: {year_from} - To: {year_to}",
            f"Top Lang : {top_lang.get('name', 'N/A')} ({float(top_lang.get('percent', 0) or 0):.2f}%)",
        )
    )
    lines.append(
        two_col(
            f"{repos_total} repos ({repos_public} public, {repos_private} private), {stars} stars",
            f"Top Editor: {top_editor.get('name', 'N/A')} ({float(top_editor.get('percent', 0) or 0):.2f}%)",
        )
    )
    lines.append(f"WakaTime (last 7d): {total_display} total")
    lines.append(f"Daily Average: {daily_display}")
    lines.append(right_col(f"Peak Time: {peak_time_name} ({(peak_time_value / week_total * 100) if week_total else 0:.2f}%)"))
    lines.append(right_col(f"Peak Day : {peak_day_name} ({(peak_day_value / week_total * 100) if week_total else 0:.2f}%)"))
    lines.append(right_col(f"Activity : {activity_chunks} chunks"))
    lines.append("")
    lines.append("Stats & Proficiency")
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("")
    lines.append(" Languages")

    if langs:
        for i, language in enumerate(langs[:MAX_LANGUAGES]):
            name = str(language.get("name", "Unknown"))
            pct = float(language.get("percent", 0) or 0)
            sec = float(language.get("total_seconds", 0) or 0)
            quote = language_quotes[i] if i < len(language_quotes) else ""
            left = format_metric_row(name, pct, sec)
            lines.append(two_col(left, quote))
    else:
        lines.append(" No language activity returned by WakaTime for the current report window.")

    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("")
    lines.append(" I Code Most During")
    lines.append("")
    if used_duration_fallback or not durations_available:
        lines.append(" (time buckets fallback applied: detailed durations unavailable)")
        lines.append("")

    for idx, (name, window) in enumerate(periods):
        sec = bucket_seconds[name]
        pct = (sec / week_total * 100) if week_total else 0
        left = format_metric_row(f"{name} ({window})", pct, sec)
        lines.append(two_col(left, period_quotes[idx]))

    lines.append("")
    lines.append(" I Am Most Productive On")
    lines.append("")

    for day_name in weekdays:
        sec = weekday_seconds.get(day_name, 0.0)
        pct = (sec / week_total * 100) if week_total else 0
        left = format_metric_row(day_name, pct, sec, label_width=10)
        lines.append(two_col(left, day_quotes.get(day_name, "")))

    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("")
    lines.append(" Editors and Operating Systems")
    lines.append(" Editors")
    if editors:
        for row in editors[:MAX_EDITORS_OR_OS]:
            name = str(row.get("name", "Unknown"))
            pct = float(row.get("percent", 0) or 0)
            sec = float(row.get("total_seconds", 0) or 0)
            note = platform_note(name)
            left = format_metric_row(name, pct, sec)
            lines.append(two_col(left, note))
    else:
        lines.append(" No editor activity returned by WakaTime.")
    lines.append(" Operating Systems")
    if oses:
        for row in oses[:MAX_EDITORS_OR_OS]:
            name = str(row.get("name", "Unknown"))
            pct = float(row.get("percent", 0) or 0)
            sec = float(row.get("total_seconds", 0) or 0)
            note = platform_note(name)
            left = format_metric_row(name, pct, sec)
            lines.append(two_col(left, note))
    else:
        lines.append(" No operating system activity returned by WakaTime.")

    timestamp = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("")
    lines.append(
        f" Languages/Time/Day/Editors/OS from WakaTime API · Repo stats from GitHub API · Updated: {timestamp}"
    )
    return "```text\n" + "\n".join(lines) + "\n```"


def update_readme_section(content: str, new_block: str) -> str:
    start_idx = content.find(START_MARKER)
    end_idx = content.find(END_MARKER)
    if start_idx == -1 or end_idx == -1:
        raise ValueError("WakaTime section markers are missing in README.md")
    if end_idx < start_idx:
        raise ValueError("WakaTime section markers are incorrectly ordered in README.md")
    before = content[: start_idx + len(START_MARKER)]
    after = content[end_idx:]
    return f"{before}\n{new_block}\n{after}"


def main() -> None:
    current = README_PATH.read_text(encoding="utf-8")
    new_block = build_metrics_block()
    updated = update_readme_section(current, new_block)
    README_PATH.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    main()
