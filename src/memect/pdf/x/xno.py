import re
from typing import Any, Callable, Final

from memect.base.pattern import XPattern

# Each rule: (pattern, to_int_fn)
# to_int_fn converts the captured group to an integer index

type ParseResult = tuple[str, int, str]
type RuleHandler = Callable[[re.Match[str]], ParseResult]
type Rule = tuple[re.Pattern[str], RuleHandler]


def _cn_to_int(s: str) -> int:
    digits: dict[str, int] = {
        "零": 0,
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    s = s.strip()
    if s in digits:
        return digits[s]
    if s.startswith("十"):
        return 10 + (digits.get(s[1:], 0) if len(s) > 1 else 0)
    if "十" in s:
        i = s.index("十")
        tens = digits.get(s[:i], 1) * 10
        ones = digits.get(s[i + 1 :], 0) if i + 1 < len(s) else 0
        return tens + ones
    return 0


def _jp_to_int(s: str) -> int:
    # Japanese uses same CJK numerals
    return _cn_to_int(s)


def _kr_to_int(s: str) -> int:
    kr: dict[str, int] = {
        "일": 1,
        "이": 2,
        "삼": 3,
        "사": 4,
        "오": 5,
        "육": 6,
        "칠": 7,
        "팔": 8,
        "구": 9,
        "십": 10,
    }
    s = s.strip()
    if s in kr:
        return kr[s]
    if s.startswith("십"):
        return 10 + (kr.get(s[1:], 0) if len(s) > 1 else 0)
    return 0


_CN_NUM = r"[零一二三四五六七八九十百千]+"
_JP_NUM = r"[零一二三四五六七八九十百千]+"
_KR_NUM = r"(?:십|일|이|삼|사|오|육|칠|팔|구)+"

RULES: list[
    tuple[
        re.Pattern[str],
        Callable[[re.Match[str]], tuple[str, int, str]],
    ]
] = [
    # 中文章节：第一章、第二节、第三篇 等
    (
        re.compile(r"^(第(" + _CN_NUM + r")[章节篇条款项])[\s　]"),
        lambda m: (
            m.group(1),
            _cn_to_int(m.group(2)),
            r"第" + _CN_NUM + r"[章节篇条款项]",
        ),
    ),
    # 中文序号带顿号：一、二、三、
    (
        re.compile(r"^((" + _CN_NUM + r")[、])"),
        lambda m: (m.group(1), _cn_to_int(m.group(2)), _CN_NUM + r"[、]"),
    ),
    # 中文括号序号：（一）（二）
    (
        re.compile(r"^([（(](" + _CN_NUM + r")[）)])"),
        lambda m: (m.group(1), _cn_to_int(m.group(2)), r"[（(]" + _CN_NUM + r"[）)]"),
    ),
    # 繁体同上（共用 CJK 数字规则，已覆盖）
    # 日语：第一章、第二節
    (
        re.compile(r"^(第(" + _JP_NUM + r")[章節項条])[\s　]"),
        lambda m: (m.group(1), _jp_to_int(m.group(2)), r"第" + _JP_NUM + r"[章節項条]"),
    ),
    # 韩语：제일장、제이절
    (
        re.compile(r"^(제(" + _KR_NUM + r")[장절항])[\s ]"),
        lambda m: (m.group(1), _kr_to_int(m.group(2)), r"제" + _KR_NUM + r"[장절항]"),
    ),
    # 阿拉伯数字 + 点：1. 2. 10.
    (re.compile(r"^((\d+)\.)[\s ]"), lambda m: (m.group(1), int(m.group(2)), r"\d+\.")),
    # 阿拉伯数字 + 顿号：1、2、
    (re.compile(r"^((\d+)[、])"), lambda m: (m.group(1), int(m.group(2)), r"\d+[、]")),
    # 带括号数字：(1) （2）
    (
        re.compile(r"^([（(](\d+)[）)])"),
        lambda m: (m.group(1), int(m.group(2)), r"[（(]\d+[）)]"),
    ),
    # 罗马数字（大写）：I. II. III.
    (
        re.compile(
            r"^((I{1,3}|IV|VI{0,3}|IX|XI{0,3}|XIV|XV|XVI{0,3}|XIX|XX)\.)[\s ]",
            re.IGNORECASE,
        ),
        lambda m: (
            m.group(1),
            _roman_to_int(m.group(2).upper()),
            r"(?:I{1,3}|IV|VI{0,3}|IX|X[IVX]*)\.",
        ),
    ),
    # 字母序号：A. B. a. b.
    (
        re.compile(r"^(([A-Za-z])\.)[\s ]"),
        lambda m: (m.group(1), ord(m.group(2).upper()) - ord("A") + 1, r"[A-Za-z]\."),
    ),
    # 字母括号：(a) (A)
    (
        re.compile(r"^([（(]([A-Za-z])[）)])"),
        lambda m: (
            m.group(1),
            ord(m.group(2).upper()) - ord("A") + 1,
            r"[（(][A-Za-z][）)]",
        ),
    ),
    # 带点多级编号：1.1 1.1.2
    (
        re.compile(r"^((\d+(?:\.\d+)+)\.)[\s ]"),
        lambda m: (m.group(1), int(m.group(2).split(".")[0]), r"\d+(?:\.\d+)+\."),
    ),
    # 全角数字：１．２．
    (
        re.compile(r"^([１２３４５６７８９０]+[．、])"),
        lambda m: (
            m.group(1),
            int(
                m.group(1)[:-1].translate(
                    str.maketrans("１２３４５６７８９０", "1234567890")
                )
            ),
            r"[１２３４５６７８９０]+[．、]",
        ),
    ),
]

_ROMAN: dict[str, int] = {
    "I": 1,
    "V": 5,
    "X": 10,
    "L": 50,
    "C": 100,
    "D": 500,
    "M": 1000,
}


def _roman_to_int(s: str) -> int:
    total, prev = 0, 0
    for c in reversed(s):
        v = _ROMAN.get(c, 0)
        total += v if v >= prev else -v
        prev = v
    return total

_value_pattern: Final = XPattern(
    'search',
    join=False,
    patterns=[
        #这个是最宽松的，支持混合多级序号，如：一.1.a
        #(1.2.) => ['1','2']
        #(1.3)  => ['1','3']
        #(i.ii.iv) => ['i','ii','iv']
        r'([.]?(([0-9]+)|([a-z]+)|([A-Z]+)|([〇一二三四五六七八九十零百千]+)))+',
        #
        # ⑴-⒇
        r'[\u2474-\u2487]',
        # ①-⑳
        r'[\u2460-\u2473]',
        #如果是英文的，还有
        #1st,2nd,3rd,4th----20th
        #21st,22nd,23rd,24th-30th
        #31st,32nd,33rd,34th-40th
    ])
class XNoParser:
    def __init__(self) -> None:
        self._rules: list[Rule] = list(RULES)

    def add_rule(self, pattern: re.Pattern[str], handler: RuleHandler) -> None:
        """Add a custom rule. handler(match) -> (prefix, index, pattern_str)"""
        self._rules.append((pattern, handler))

    def parse(self, text: str) -> ParseResult | None:
        """
        Returns (prefix, index, pattern) or None.
        e.g. '第一章 xxxx' -> ('第一章', 1, r'第[零一二三四五六七八九十百千]+[章节篇条款项]')
        """
        for pattern, handler in self._rules:
            m = pattern.match(text)
            if m:
                return handler(m)
        return None
