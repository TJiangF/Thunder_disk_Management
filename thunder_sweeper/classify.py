"""Heuristic classifier for 迅雷云盘 files.

Content cannot be watched, so classification relies on file/folder names plus
the naming conventions of the various sources (JAV studio codes, site domain
prefixes, studio keywords).  Everything is heuristic: unmatched -> ``unknown``.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------- #
# Category labels
# --------------------------------------------------------------------------- #
LABELS = {
    "jp": "日本",
    "west": "欧美",
    "cn": "国产",
    "adult_other": "成人-其他",
    "non_adult": "非成人",
    "unknown": "未知",
}

# --------------------------------------------------------------------------- #
# 国产 (Chinese amateur / studio)
# --------------------------------------------------------------------------- #
CN_KEYWORDS = [
    "麻豆", "天美", "星空", "糖心", "海角", "探花", "唐伯虎", "精东", "蜜桃",
    "传媒", "国产", "皇家", "兔子", "果冻", "爱豆", "小仙儿", "扣扣", "麻豆传媒",
    "天美传媒", "星空传媒", "精东影业", "国模", "私拍", "流出", "华语", "普通话",
    "中文对白", "麻豆映画", "壹传媒", "映画", "女神", "啪先生", "迷奸", "偷拍自拍",
    "91porn", "91cm", "91探花", "91国产", "91大神", "91制片厂", "swag", "onlyfans",
    "色花堂", "sehuatang", "草榴", "1024", "性吧", "第一会所", "老王", "主播",
    "约炮", "露出", "自拍", "偷拍", "网红", "门事件", "反差", "福利", "原创",
    "推特", "淫妻", "内射", "直播", "台湾", "夫妻", "黑丝", "调教", "性奴",
    "绿帽", "骚", "裸聊", "自慰", "母狗", "呻吟", "浪叫", "无套", "口交",
    "做爱", "啪啪", "同城", "极品", "颜值", "少女", "学生妹", "嫩模", "空姐",
    "出轨", "乱伦", "换妻", "群交", "双飞", "诱惑", "月新档", "付费资源",
]
# studio/番号 prefixes that are Chinese, not JAV
CN_CODE_PREFIXES = [
    "MD", "MDX", "MDWP", "MDX", "TMW", "XKG", "GN", "JD", "MT", "SWAG",
    "ID", "HH", "RM", "PS", "TM", "XK", "MX", "QL", "MDL", "MDHG",
]

# --------------------------------------------------------------------------- #
# 日本 (JAV)
# --------------------------------------------------------------------------- #
JP_STUDIO_PREFIXES = [
    # major studios / labels
    "JUQ", "JUR", "JUL", "JUFE", "JUX", "JUNY", "JUBE",
    "SONE", "SSIS", "SSNI", "SSIN", "STARS", "STAR", "START",
    "IPX", "IPZZ", "IPZ", "IPIT", "IDBD", "IDEA", "MIDE", "MIGD", "MIMK", "MIAA",
    "MEYD", "MIBB", "MIDV", "MIFD", "MIAE",
    "ABF", "ABW", "ABP", "ABW",
    "DLDSS", "DLPN", "FSDSS", "FSD", "SSD",
    "PRED", "PRST", "MONG", "MUKD", "HMN", "MUKC",
    "STP", "SGA", "KBI", "KBR", "SUKA", "SUKE", "VDD", "ROE", "ROYD",
    "ALDN", "AARM", "AARM", "GVH", "GVH", "NHDT", "NHDTA", "NTK", "NSPS",
    "MKMP", "MKON", "WANZ", "WAAA", "RCT", "RCTD", "MIAD", "MIUM", "MUDR",
    "ADN", "ATID", "BF", "CAWD", "CJOD", "DASD", "DDT", "DVAJ", "EBOD",
    "FAA", "FLAV", "GENM", "GIGL", "GVG", "HAWA", "HND", "HODV", "HOMA",
    "HUNT", "IENE", "IESP", "JJDA", "JUL", "KAWD", "KIRE", "KUSE",
    "LULU", "MADM", "MAGT", "MAKO", "MEYD", "MIA", "MIAA", "MIDE",
    "MIHA", "MIRD", "MIST", "MMGH", "MOKO", "MOND", "MOT", "MUD",
    "NACR", "NASH", "NGOD", "NITR", "NKKD", "NNPJ", "NSFS", "NTR",
    "OFJE", "OKS", "ONGP", "OYC", "PKPD", "PPPD", "PRED", "REAL",
    "RKI", "RTP", "SBMO", "SDAB", "SDDE", "SDMT", "SGKI", "SHKD",
    "SIRO", "SMA", "SNIS", "SOE", "SQTE", "SSPD", "STARS", "STCV",
    "TEK", "TIKB", "TURA", "UMSO", "VAGU", "VEC", "VENU", "VRTM",
    "WA", "XRW", "YSN", "ZIZG", "ZUKO", "ZEX",
]
JP_KEYWORDS = [
    "jav", "無修正", "无修正", "中文字幕", "字幕", "独占", "配信", "素人",
    "人妻", "痴女", "熟女", "中出", "顔射", "颜射", "巨乳", "制服", "女优",
    "女優", "作品", "番号", "有码", "有碼", "无码", "無碼", "流出", "单体",
    "单体作品", "fhd", "uncensored", "leaked", "japan", "japanese",
    "tokyo", "东京", "東京",
    # JAV sub-labels / amateur series
    "fc2", "fc2-ppv", "ppv", "carib", "caribbean", "1pondo", "heyzo",
    "10musume", "pacopacomama", "tokyo-hot", "tokyohot", "muramura",
    "gachinco", "heydouga", "luxu", "nyap2p", "gana", "manko", "mium",
    "mgstage", "prestige", "moodyz", "kawaii", "tameike",
]
JP_DOMAIN_PREFIXES = [
    "hhd800.com", "489155.com", "98t.la", "kfa55.com", "kfa33.com", "kfa11.com",
    "aavv38.xyz", "aavv39.xyz", "aavv40.xyz", "aavv121.com", "aavv37.xyz",
    "avav36.xyz", "avav55.xyz", "zzpp01.com", "zzpp06.com", "jav20s8.com",
    "rh2048.com", "fun2048.com", "woxav.com", "kcf9.com", "d66e.com",
    "kckc13.com", "5nvw.com", "4k688.com", "4k2.", "mm616", "plutonie",
    "jav", "javbus", "javdb", "sextb", "avmoo", "avsox",
    "aavv", "avav", "zzpp", "kfa",
]

# --------------------------------------------------------------------------- #
# 欧美 (Western)
# --------------------------------------------------------------------------- #
WEST_KEYWORDS = [
    "brazzers", "blacked", "blackedraw", "vixen", "tushy", "deeper", "slayed",
    "bangbros", "bangbus", "realitykings", "reality kings", "propertysex",
    "pornhub", "xvideos", "x-art", "xart", "sexart", "passion-hd", "passionhd",
    "nubile", "nubiles", "teamskeet", "team skeet", "milf", "milfy", "anal",
    "teen", "stepsis", "stepmom", "stepsister", "stepbro", "big tits", "bigtits",
    "interracial", "gangbang", "pov", "hentai", "xxx", "onlyfans",
    "dorcel", "dorcelclub", "woodmancastingx", "tonightsgirlfriend", "tabooheat",
    "mylflabs", "mylf", "digitalplayground", "digital playground", "naughtyamerica",
    "naughty america", "evilangel", "evil angel", "julesjordan", "jules jordan",
    "newsensations", "new sensations", "allblack", "hegre", "met-art", "metart",
    "fitnessrooms", "bratty", "babysitter", "casting", "private", "sensual",
    "momswap", "penthouse", "penthousegold", "lewd", "familytherapy",
    "castingcouch", "casting couch", "trueanal", "true anal", "allanal",
    "swallow", "girlsway", "girlfriendsfilms", "sweetheartvideo", "wowgirls",
    "vivthomas", "joymii", "ersties", "hardcore", "porn", "sex",
]

# --------------------------------------------------------------------------- #
# 非成人 (documentaries / series / courses / music etc.)
# --------------------------------------------------------------------------- #
NON_ADULT_KEYWORDS = [
    "第", "集", "全集", "电视剧", "连续剧", "综艺", "动漫", "动画", "纪录片",
    "教程", "课程", "公开课", "讲座", "网课", "培训", "考试", "音乐", "演唱",
    "mv", "live", "concert", "movie", "film", "bluray", "blu-ray", "remux",
    "web-dl", "webrip", "hdtv", "x264", "x265", "hevc", "纪录片", "电影",
]

_CODE_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2,6})[-_ ]?(\d{2,5})(?![0-9])")

NON_ADULT_EXTS = {
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "epub", "mobi",
    "zip", "rar", "7z", "tar", "gz", "iso", "apk", "exe", "dmg", "pkg", "deb",
    "rpm", "jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "mp3", "wav",
    "flac", "aac", "m4a", "ape", "srt", "ass", "ssa", "json", "xml", "csv",
    "psd", "ai", "sketch", "torrent",
}


def _ext(name: str) -> str:
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()


def _has_code(name: str, prefixes: set[str]) -> bool:
    for m in _CODE_RE.finditer(name):
        if m.group(1) in prefixes:
            return True
    return False


# --------------------------------------------------------------------------- #
# user rules (editable from the dashboard / data/classify_rules.json)
# --------------------------------------------------------------------------- #
def load_user_rules() -> dict:
    from . import categories, util

    rules = util.read_json(util.CLASSIFY_RULES_FILE, {}) or {}
    valid = categories.ids()
    extra = rules.get("extra") or {}
    clean_extra = {k: [str(x) for x in (v or []) if str(x).strip()]
                   for k, v in extra.items() if k in valid or k in LABELS}
    overrides = []
    for ov in rules.get("overrides") or []:
        kw = str(ov.get("keyword") or "").strip()
        cat = ov.get("category")
        if kw and (cat in valid or cat in LABELS):
            overrides.append({"keyword": kw, "category": cat})
    return {"extra": clean_extra, "overrides": overrides}


def save_user_rules(rules: dict) -> None:
    from . import util

    util.atomic_write_json(util.CLASSIFY_RULES_FILE, rules)


def _keywords(base: list[str], cat: str, rules: dict) -> list[str]:
    return list(base) + list((rules.get("extra") or {}).get(cat, []))


def classify(name: str, path: str = "", rules: dict | None = None) -> tuple[str, str]:
    """Return (category, reason)."""
    if rules is None:
        rules = load_user_rules()

    label = f"{path} {name}".lower()
    upper = f"{path} {name}"

    # non-video / document / archive files are not adult movies
    if _ext(name) in NON_ADULT_EXTS:
        return "non_adult", f"ext:{_ext(name)}"

    # user overrides win first
    for ov in rules.get("overrides") or []:
        kw = str(ov.get("keyword") or "").strip().lower()
        if kw and kw in label:
            return ov["category"], f"override:{kw}"

    # explicit folder hints first
    if re.search(r"(日本|jav|日系)", label):
        return "jp", "folder/jp-hint"
    if re.search(r"(欧美|western|英文)", label):
        return "west", "folder/west-hint"
    if re.search(r"(国产|国产自拍|华语|中文)", label):
        return "cn", "folder/cn-hint"

    # 国产
    if _has_code(upper, set(CN_CODE_PREFIXES)):
        return "cn", "cn-code"
    for kw in _keywords(CN_KEYWORDS, "cn", rules):
        if kw.lower() in label:
            return "cn", f"cn:{kw}"

    # 日本
    if _has_code(upper, set(JP_STUDIO_PREFIXES)):
        return "jp", "jp-code"
    for kw in _keywords(JP_DOMAIN_PREFIXES, "jp", rules):
        if kw in label:
            return "jp", f"jp-domain:{kw}"
    for kw in _keywords(JP_KEYWORDS, "jp", rules):
        if kw.lower() in label:
            return "jp", f"jp:{kw}"

    # 欧美
    for kw in _keywords(WEST_KEYWORDS, "west", rules):
        if kw in label:
            return "west", f"west:{kw}"

    # 非成人
    for kw in _keywords(NON_ADULT_KEYWORDS, "non_adult", rules):
        if kw.lower() in label:
            return "non_adult", f"non-adult:{kw}"

    # fallback: pure-latin multi-word titles -> western
    if not re.search(r"[\u4e00-\u9fff]", name):
        words = re.findall(r"[A-Za-z]{3,}", name)
        if len(words) >= 2:
            return "west", "en-fallback"

    # generic AV pattern (any studio code) -> adult but region unknown
    if _CODE_RE.search(upper):
        return "adult_other", "generic-code"
    if any(k in label for k in ("无码", "無碼", "有码", "有碼", "成人", "porn", "sex", "av")):
        return "adult_other", "generic-adult"

    return "unknown", "no-match"


def load_manual() -> dict:
    from . import util

    m = util.read_json(util.MANUAL_CATS_FILE, {}) or {}
    return {k: v for k, v in m.items() if v in LABELS}


def save_manual(m: dict) -> None:
    from . import util

    util.atomic_write_json(util.MANUAL_CATS_FILE, m)


def categorize_files(files: list[dict], rules: dict | None = None,
                     manual: dict | None = None) -> list[dict]:
    if rules is None:
        rules = load_user_rules()
    if manual is None:
        manual = load_manual()
    out = []
    for f in files:
        fid = f.get("id")
        auto, _ = classify(f.get("name") or "", f.get("path") or "", rules)
        is_manual = fid in manual
        category = manual[fid] if is_manual else auto
        item = dict(f)
        item["category"] = category
        item["auto_category"] = auto
        item["category_reason"] = "manual" if is_manual else auto
        item["manual"] = is_manual
        out.append(item)
    return out


def summarize(classified: list[dict]) -> dict:
    stats: dict[str, dict] = {}
    for item in classified:
        cat = item.get("category", "unknown")
        entry = stats.setdefault(cat, {"count": 0, "bytes": 0})
        entry["count"] += 1
        entry["bytes"] += int(item.get("size") or 0)
    return stats
