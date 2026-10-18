"""Deterministic, read-only global matching of meaningful S2 expressions."""
from __future__ import annotations

import re
from collections import defaultdict
from functools import lru_cache

try:
    from zhconv import convert
except ImportError:
    # Minimal installations still recognize the traditional forms in our domain
    # vocabulary; the normal packaged dependency provides broader conversion.
    _TRADITIONAL = str.maketrans({"個": "个", "種": "种", "兩": "两", "顏": "颜", "褲": "裤",
        "這": "这", "為": "为", "給": "给", "難": "难", "軟": "软", "顯": "显", "輕": "轻",
        "質": "质", "實": "实", "貴": "贵", "彈": "弹", "鬆": "松", "寬": "宽", "適": "适",
        "齡": "龄", "絨": "绒", "領": "领", "來": "来", "條": "条", "點": "点", "會": "会",
        "沒": "没", "現": "现", "庫": "库", "斷": "断", "補": "补", "貨": "货", "嗎": "吗"})

    def convert(text, _locale):
        return text.translate(_TRADITIONAL)

# Explicit vocabulary keeps partial matches explainable; no arbitrary substrings.
DEGREES = ("超级", "非常", "特别", "十分", "相当", "真的", "很", "太", "挺", "极其")
DESCRIPTIONS = (
    "难做", "好做", "好看", "难看", "保暖", "暖和", "显瘦", "显胖", "显高", "显白",
    "百搭", "柔软", "舒服", "舒适", "透气", "轻薄", "厚实", "轻盈", "亲肤", "耐穿",
    "耐看", "耐洗", "划算", "实惠", "便宜", "贵", "高级", "精致", "洋气", "有质感",
    "有弹性", "有分量", "有垂感", "有型", "蓬松", "修身", "宽松", "好搭配",
    "不挑身材", "不挑年龄", "不挑肤色", "不挑人", "不起球", "不掉色", "不掉毛",
)
SUBJECTS = ("面料", "版型", "做工", "工艺", "颜色", "款式", "质感", "手感", "上身", "穿着")
QUALIFIERS = (
    ("商品", ("羽绒服", "连衣裙", "半身裙", "牛仔裤", "衬衫", "毛衣", "外套", "马甲", "裤子")),
    ("颜色", ("粉色", "白色", "黑色", "红色", "蓝色", "绿色", "灰色", "米色", "紫色", "黄色")),
    ("部位", ("袖子", "领口", "袖口", "下摆", "肩膀", "腰部", "口袋", "拉链")),
)


def _alternatives(words):
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


_DEGREE_RE = re.compile(
    rf"(?P<degree>{_alternatives(DEGREES)})(?:(?P=degree))*(?:的)?(?P<description>{_alternatives(DESCRIPTIONS)})")
_SUBJECT_RE = re.compile(
    rf"(?P<subject>{_alternatives(SUBJECTS)})(?:是|的)?(?P<degree>{_alternatives(DEGREES)})?(?:的)?"
    rf"(?P<description>{_alternatives(DESCRIPTIONS)})")
_BARE_RE = re.compile(_alternatives(w for w in DESCRIPTIONS if len(w) >= 4))
_NEGATION_RE = re.compile(r"(?:不是|并非|并不|不|没|没有|未|不能|不算)(?:真的|那么|这么|算是|是|觉得|感觉|就|会|能|太|很|特别|非常|超级|的)*$")
_SEGMENTS_RE = re.compile(r"[^，,。！？!?；;：:\r\n]+")

# Attribute facts compare information, rather than the exact surrounding wording.
_COLOR_COUNT_RE = re.compile(r"(?P<number>[1-9]|10|一|二|两|三|四|五|六|七|八|九|十)(?:个|种|款)?(?:颜色|色)")
_NUMBERS = {word: i for i, word in enumerate("一二三四五六七八九十", 1)} | {"两": 2}
_PRODUCT_ALIASES = {
    "五分裤": "裤子", "5分裤": "裤子", "中裤": "裤子", "牛仔裤": "裤子", "皮裤": "裤子", "裤子": "裤子",
    "羽绒服": "羽绒服", "皮衣": "皮衣", "连衣裙": "连衣裙", "半身裙": "半身裙", "衬衫": "衬衫",
    "毛衣": "毛衣", "外套": "外套", "马甲": "马甲",
}
_PRODUCT_RE = re.compile(_alternatives(_PRODUCT_ALIASES))
_PRODUCT_SWITCH_RE = re.compile(rf"(?:下一款|下一件|接下来介绍|现在介绍|换成)(?:是|的|这款|这件)?(?P<product>{_alternatives(_PRODUCT_ALIASES)})")
_COLOR_ALIASES = {"黑色": "黑色", "白色": "白色", "咖色": "咖色", "咖啡色": "咖色", "卡色": "咖色",
                  "粉色": "粉色", "红色": "红色", "蓝色": "蓝色", "绿色": "绿色", "灰色": "灰色",
                  "米色": "米色", "紫色": "紫色", "黄色": "黄色"}
_COLOR_RE = re.compile(_alternatives(_COLOR_ALIASES))
_SHORT_COLOR_RE = re.compile(r"(?:一个|另外一个|和|跟|与|、)(黑|白|咖|卡)(?![\u4e00-\u9fff])|(?:一个)(黑|白|咖|卡)(?=然后|一个|$)")
_COLOR_PAIR_RE = re.compile(r"(?P<first>黑|白|咖|卡)(?:跟|和|与|、)(?P<second>黑|白|咖|卡)(?=色|两|二|2|$)")
_SHORT_COLORS = {"黑": "黑色", "白": "白色", "咖": "咖色", "卡": "咖色"}


@lru_cache(maxsize=4096)
def _simplified(char):
    return convert(char, "zh-cn").lower()


def _canonical(normalized):
    # Remove only the optional connector in an explicitly recognized structure.
    normalized = _DEGREE_RE.sub(lambda m: m["degree"] + m["description"], normalized)
    return _SUBJECT_RE.sub(lambda m: m["subject"] + (m["degree"] or "") + m["description"], normalized)


def _characters(text):
    """Ignore punctuation/spacing, retaining original character offsets."""
    chars, positions = [], []
    for index, char in enumerate(text):
        if char.isalnum():
            for normalized in _simplified(char):
                chars.append(normalized)
                positions.append(index)
    return "".join(chars), positions


def trial_options(payload):
    mode = payload.get("mode", "all")
    if mode not in ("all", "word", "sentence", "fact"):
        raise ValueError("请选择有效的重复检测类型")
    options = {"mode": mode}
    for name, default, low, high, label in (
        ("min_word_length", 4, 2, 32, "有效短语最少字数"),
        ("min_sentence_length", 4, 2, 200, "语句最少字数"),
        ("min_count", 2, 2, 10, "最少出现子句数"),
    ):
        value = payload.get(name, default)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"{label}须为 {low}–{high} 的整数")
        options[name] = value
    return options


def _phrases(text, minimum):
    row_text, _ = _characters(text)
    row_context = tuple((name, word) for name, words in QUALIFIERS for word in words if word in row_text)
    for segment in _SEGMENTS_RE.finditer(text):
        normalized, positions = _characters(segment.group())
        context = tuple((name, word) for name, words in QUALIFIERS for word in words if word in normalized)
        if not context:
            context = row_context
        candidates = []
        for pattern in (_SUBJECT_RE, _DEGREE_RE, _BARE_RE):
            for match in pattern.finditer(normalized):
                # A negated or qualified negative statement is not a positive claim.
                if _NEGATION_RE.search(normalized[:match.start()]):
                    continue
                key = _canonical(match.group())
                if len(key) < minimum:
                    continue
                candidates.append((match.start(), match.end(), key))
        candidates.sort(key=lambda c: (-(c[1] - c[0]), c[0]))
        selected = []
        for start, end, key in candidates:
            if any(start < previous[1] and previous[0] < end for previous in selected):
                continue
            selected.append((start, end, key))
            yield {"phrase": key, "context": context,
                   "start": segment.start() + positions[start],
                   "end": segment.start() + positions[end - 1] + 1}


def _color_facts(text, active_product):
    """Count facts are independent of worn-color claims; never invent missing colors.

    An explicit product at a color-offering statement anchors following implicit
    statements. Mere styling mentions elsewhere do not change that anchor.
    """
    for sentence in re.finditer(r"[^。！？!?；;\r\n]+", text):
        normalized, positions = _characters(sentence.group())
        for match in _COLOR_COUNT_RE.finditer(normalized):
            before = normalized[max(0, match.start() - 16):match.start()]
            after = normalized[match.end():match.end() + 4]
            if (_NEGATION_RE.search(before) or re.search(r"(?:剩|库存|缺货|卖完|断货|补货)", before)
                    or after.startswith(("吗", "么"))
                    or text[sentence.end():sentence.end() + 1] in ("?", "？")):
                continue
            products = list(_PRODUCT_RE.finditer(before))
            if products:
                active_product = _PRODUCT_ALIASES[products[-1].group()]
            number = match["number"]
            count = int(number) if number.isdigit() else _NUMBERS[number]
            start = sentence.start() + positions[match.start()]
            end = sentence.start() + positions[match.end() - 1] + 1
            colors = {_COLOR_ALIASES[m.group()] for m in _COLOR_RE.finditer(normalized)}
            for pair in _COLOR_PAIR_RE.finditer(normalized):
                colors.update((_SHORT_COLORS[pair["first"]], _SHORT_COLORS[pair["second"]]))
            for color in _SHORT_COLOR_RE.finditer(normalized):
                colors.add(_SHORT_COLORS[color[1] or color[2]])
            # A partial list or an ASR error must not become a claimed complete set.
            complete = sorted(colors) if len(colors) == count else []
            yield {"phrase": f"可选颜色：{count}种", "context": (("商品", active_product),) if active_product else (),
                   "start": start, "end": end, "product": active_product,
                   "fact": {"attribute": "color_count", "value": count, "complete_colors": complete,
                            "observed_colors": sorted(colors)}}


def match_rows(rows, options):
    results = [{"id": r.get("id"), "text": str(r.get("text") or ""),
                "start": r.get("start"), "end": r.get("end"),
                "s2_usable": bool(r.get("usable")), "s2_reason": r.get("reason", ""),
                "hits": []} for r in rows]
    results.sort(key=lambda r: float(r.get("start") or 0))
    candidates = defaultdict(list)
    active_product = ""
    for row_index, row in enumerate(results):
        # Apply the existing S2 decision before indexing any text or object context.
        # Rejected rows remain visible in the trial, but cannot seed repetitions.
        if not row["s2_usable"]:
            continue
        if options["mode"] in ("all", "fact"):
            simplified, _ = _characters(row["text"])
            switches = list(_PRODUCT_SWITCH_RE.finditer(simplified))
            if switches:
                active_product = _PRODUCT_ALIASES[switches[-1]["product"]]
            for fact in _color_facts(row["text"], active_product):
                active_product = fact["product"]
                candidates[("fact", fact["phrase"])].append({**fact, "row_index": row_index})
        if options["mode"] in ("all", "word"):
            for phrase in _phrases(row["text"], options["min_word_length"]):
                candidates[("word", phrase["phrase"])].append({**phrase, "row_index": row_index})
        if options["mode"] in ("all", "sentence"):
            # Chinese/Latin sentence punctuation and newlines delimit utterances.
            for part in re.finditer(r"[^。！？!?；;\r\n]+", row["text"]):
                normalized, positions = _characters(part.group())
                key = _canonical(normalized)
                if len(key) >= options["min_sentence_length"]:
                    start = part.start() + positions[0]
                    end = part.start() + positions[-1] + 1
                    candidates[("sentence", key)].append({"row_index": row_index, "start": start, "end": end, "context": ()})
    groups = []
    for (kind, key), occurrences in candidates.items():
        contexts = {o["context"] for o in occurrences if o["context"]}
        buckets = defaultdict(list)
        for occurrence in occurrences:
            context = occurrence["context"]
            # Generic mentions can join one unambiguous object, but never bridge
            # conflicting colors/products/parts into a single repetition group.
            if not context and len(contexts) == 1:
                context = next(iter(contexts))
            buckets[context].append(occurrence)
        scoped_buckets = []
        for context, bucket in buckets.items():
            if kind != "fact":
                scoped_buckets.append((context, bucket))
                continue
            complete_sets = {tuple(o["fact"]["complete_colors"]) for o in bucket if o["fact"]["complete_colors"]}
            color_buckets = defaultdict(list)
            for occurrence in bucket:
                fact = occurrence["fact"]
                compatible = [colors for colors in complete_sets if set(fact["observed_colors"]) <= set(colors)]
                signature = tuple(fact["complete_colors"]) or (compatible[0] if len(compatible) == 1 else ())
                # Incompatible partial colors must not bridge a conflicting set.
                if not signature and complete_sets:
                    signature = ("未确认:" + "/".join(fact["observed_colors"]),)
                color_buckets[signature].append(occurrence)
            for colors, members in color_buckets.items():
                scoped = context + (("颜色集合", "/".join(colors)),) if colors and not colors[0].startswith("未确认:") else context
                scoped_buckets.append((scoped, members))
        for context, bucket in scoped_buckets:
            count = len({o["row_index"] for o in bucket})
            if count < options["min_count"]:
                continue
            group_id = f"repeat-{len(groups) + 1}"
            members = []
            for occurrence in bucket:
                row = results[occurrence["row_index"]]
                start, end = occurrence["start"], occurrence["end"]
                members.append({"row_index": occurrence["row_index"], "id": row["id"],
                                "start": row["start"], "end": row["end"], "text": row["text"][start:end],
                                "full_text": row["text"], "char_start": start, "char_end": end,
                                **({"fact": occurrence["fact"]} if "fact" in occurrence else {})})
            group = {"id": group_id, "type": kind, "phrase": key, "count": count,
                     "context": dict(context), "occurrences": members,
                     "reason": {"word": "有效短语归一化后相同", "sentence": "完整语句归一化后相同",
                                "fact": "同一商品的可选颜色数量相同；只标记重复信息点，句内其他信息可能不同"}[kind]}
            groups.append(group)
            for member in members:
                row = results[member["row_index"]]
                row["hits"].append({"group_id": group_id, "type": kind, "text": member["text"],
                                    "phrase": key, "count": count, "start": member["char_start"],
                                    "end": member["char_end"], "reference": members[0],
                                    "is_first": member["row_index"] == members[0]["row_index"]})
    eligible = sum(r["s2_usable"] for r in results)
    return {"options": options, "total": len(results), "eligible": eligible,
            "excluded": len(results) - eligible,
            "matched": sum(bool(r["hits"]) for r in results),
            "newly_rejected": sum(any(h["type"] == "sentence" and not h["is_first"] for h in r["hits"])
                                  and r["s2_usable"] for r in results),
            "usable_matched": sum(bool(r["hits"]) and r["s2_usable"] for r in results),
            "word_matched": sum(any(h["type"] == "word" for h in r["hits"]) for r in results),
            "sentence_matched": sum(any(h["type"] == "sentence" for h in r["hits"]) for r in results),
            "fact_matched": sum(any(h["type"] == "fact" for h in r["hits"]) for r in results),
            "group_count": len(groups), "groups": groups, "rows": results}
