"""ECOS client with automatic pagination and concurrent batch fetching."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import re
from urllib.parse import quote

import httpx

from ..config import ECOS_API_ROOT, ecos_api_key

_SEGMENT = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_DATE = re.compile(r"^[0-9]{4}(?:[0-9]{2}(?:[0-9]{2})?)?$")
PAGE_SIZE = 1000
MAX_SYNC_LIMIT = 50000


class EcosError(RuntimeError):
    pass


def _segment(value: str, label: str) -> str:
    value = str(value).strip()
    if not _SEGMENT.fullmatch(value):
        raise EcosError(f"{label} 형식이 올바르지 않습니다.")
    return value


def _validate_period(cycle: str, start: str, end: str) -> tuple[str, str]:
    start = str(start).strip().upper()
    end = str(end).strip().upper()
    if cycle == "Q":
        valid = all(re.fullmatch(r"[0-9]{4}Q[1-4]", value) for value in (start, end))
    else:
        valid = bool(_DATE.fullmatch(start) and _DATE.fullmatch(end))
    expected = {"D": 8, "M": 6, "Q": 6, "A": 4}[cycle]
    if not valid or len(start) != expected or len(end) != expected or start > end:
        raise EcosError("주기에 맞는 시작·종료 기간을 올바르게 입력하세요.")
    if cycle in {"D", "M"}:
        fmt = "%Y%m%d" if cycle == "D" else "%Y%m"
        try:
            datetime.strptime(start, fmt)
            datetime.strptime(end, fmt)
        except ValueError as exc:
            raise EcosError("존재하지 않는 날짜입니다. 시작·종료 기간을 확인하세요.") from exc
    return start, end


def _build_urls(
    key: str,
    start_idx: int,
    end_idx: int,
    stat_code: str,
    cycle: str,
    start: str,
    end: str,
    item_code: str,
) -> tuple[str, str]:
    parts = [
        "StatisticSearch",
        key,
        "json",
        "kr",
        str(start_idx),
        str(end_idx),
        stat_code,
        cycle,
        start,
        end,
    ]
    if item_code:
        parts.append(item_code)
    url = ECOS_API_ROOT + "/" + "/".join(quote(part, safe="") for part in parts)
    redacted_parts = [parts[0], "REDACTED", *parts[2:]]
    redacted_url = ECOS_API_ROOT + "/" + "/".join(quote(part, safe="") for part in redacted_parts)
    return url, redacted_url


def _request_page(client: httpx.Client, url: str) -> tuple[list[dict], int]:
    try:
        response = client.get(url)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise EcosError(f"ECOS 요청 실패: HTTP {exc.response.status_code}") from exc
    except (httpx.RequestError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise EcosError("ECOS에 연결할 수 없거나 응답을 읽지 못했습니다.") from exc

    if not isinstance(payload, dict):
        raise EcosError("ECOS 응답 형식이 예상과 다릅니다.")

    body = payload.get("StatisticSearch")
    body_dict = body if isinstance(body, dict) else {}
    result = payload.get("RESULT") or body_dict.get("RESULT")
    if isinstance(result, dict) and result:
        code = str(result.get("CODE") or "")
        message = str(result.get("MESSAGE") or "")[:240]
        raise EcosError(f"ECOS 응답 오류 ({code}): {message}")

    rows = body_dict.get("row", [])
    if not isinstance(rows, list):
        raise EcosError("ECOS 응답 형식이 예상과 다릅니다.")
    try:
        total_count = int(body_dict.get("list_total_count") or len(rows))
    except (TypeError, ValueError):
        total_count = len(rows)
    return [row for row in rows if isinstance(row, dict)], total_count


def _normalize_row(row: dict, stat_code: str, cycle: str, default_item: str) -> dict[str, str]:
    codes = [str(row.get(f"ITEM_CODE{i}") or "").strip() for i in range(1, 5)]
    names = [str(row.get(f"ITEM_NAME{i}") or "").strip() for i in range(1, 5)]
    non_empty_codes = [code for code in codes if code]
    non_empty_names = [name for name in names if name]
    item_code = "/".join(non_empty_codes) if non_empty_codes else default_item
    item_name = " · ".join(non_empty_names) if non_empty_names else (default_item or "전체")
    return {
        "stat_code": str(row.get("STAT_CODE") or stat_code),
        "stat_name": str(row.get("STAT_NAME") or stat_code),
        "item_code": item_code,
        "item_name": item_name,
        "cycle": str(row.get("CYCLE") or cycle),
        "time": str(row.get("TIME") or ""),
        "value": str(row.get("DATA_VALUE") or ""),
        "unit": str(row.get("UNIT_NAME") or ""),
    }


def fetch_series(
    stat_code: str,
    cycle: str,
    start: str,
    end: str,
    item_code: str = "",
    limit: int = 500,
) -> tuple[list[dict[str, str]], str]:
    """Fetch statistical data with automatic pagination up to MAX_SYNC_LIMIT."""
    stat_code = _segment(stat_code, "통계표 코드")
    cycle = _segment(cycle.upper(), "주기")
    if cycle not in {"D", "M", "Q", "A"}:
        raise EcosError("주기는 D(일), M(월), Q(분기), A(년)만 지원합니다.")
    start, end = _validate_period(cycle, start, end)
    if not 1 <= limit <= MAX_SYNC_LIMIT:
        raise EcosError(f"한 번에 가져올 데이터는 1~{MAX_SYNC_LIMIT:,}건입니다.")
    item_code = item_code.strip()
    if item_code:
        item_code = _segment(item_code, "항목 코드")

    key = ecos_api_key() or "sample"
    target_limit = min(limit, 10) if key == "sample" else limit
    first_end = min(target_limit, PAGE_SIZE)
    first_url, redacted_url = _build_urls(key, 1, first_end, stat_code, cycle, start, end, item_code)

    headers = {"Accept": "application/json", "User-Agent": "LocalEconomyAssistant/0.2"}
    with httpx.Client(timeout=20.0, headers=headers) as client:
        raw_rows, total_count = _request_page(client, first_url)
        desired_total = min(target_limit, total_count)
        if key != "sample" and desired_total > first_end:
            page_ranges = [
                (offset, min(offset + PAGE_SIZE - 1, desired_total))
                for offset in range(first_end + 1, desired_total + 1, PAGE_SIZE)
            ]
            urls = [
                _build_urls(key, page_start, page_end, stat_code, cycle, start, end, item_code)[0]
                for page_start, page_end in page_ranges
            ]
            with ThreadPoolExecutor(max_workers=4) as pool:
                for page_rows, _ in pool.map(lambda u: _request_page(client, u), urls):
                    raw_rows.extend(page_rows)

    normalized = [_normalize_row(row, stat_code, cycle, item_code) for row in raw_rows]
    return normalized, redacted_url


def fetch_many(requests: list[dict[str, object]]) -> list[tuple[dict[str, object], list[dict[str, str]]]]:
    """Fetch multiple ECOS series concurrently."""
    def _run(spec: dict[str, object]) -> tuple[dict[str, object], list[dict[str, str]]]:
        rows, _url = fetch_series(
            str(spec["stat_code"]),
            str(spec["cycle"]),
            str(spec["start"]),
            str(spec["end"]),
            str(spec.get("item_code", "")),
            int(spec.get("limit", 1000)),
        )
        return spec, rows

    with ThreadPoolExecutor(max_workers=min(4, max(1, len(requests)))) as pool:
        return list(pool.map(_run, requests))
