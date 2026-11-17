"""Conservative Chinese query-state checks, not a general semantic validator.

Only direct searched-but-empty statements in a small grammar are recognized.
Unrecognized wording, attribution, questions and negations are left to review.
No query states or model text are changed by this module.
"""
from dataclasses import dataclass
import re


UNAVAILABLE = frozenset({'unsupported', 'not_queried', 'failed'})
ALIASES = {
    'report': ('本条报事', '起始报事'),
    'related_reports': ('其他居民报事', '相关居民报事', '相关报事', '其他报事'),
    'plans': ('相关候选计划', '候选工作计划', '候选计划', '工作计划'),
    'manual_links': ('人员补充计划', '人员补充的计划'),
    'plan_records': ('执行与现场检查记录', '执行与现场检查', '现场检查记录', '执行记录', '巡查记录'),
    'assets': ('设备及管网资料', '设备台账', '管网资料'),
    'work_orders': ('历史工单记录', '维修工单记录', '工单记录', '历史工单', '维修工单', '工单'),
    'notices': ('通知记录', '相关通知', '通知'),
}
# Longest tokens first. Noun lists must be fully parsed; matching a keyword
# anywhere in arbitrary text would wrongly reject negation and mixed clauses.
_NOUN_SOURCES = {alias: source for source, aliases in ALIASES.items() for alias in aliases}
_LINKS = ('相关', '对应', '任何', '明确', '其他', '本次', '历史', '现有', '候选',
          '可用', '的', '记录', '数据', '资料', '条目', '以及', '及', '或', '与', '和', '、')
_TOKENS = sorted(set(_NOUN_SOURCES) | set(_LINKS), key=lambda word: (-len(word), word))
_PREFIXES = sorted(('截至本次核查', '在本次查询范围内', '本次范围内', '查询结果显示',
                    '经查询', '经检索', '本次', '本轮', '当前', '目前', '系统', '且', '并且', '并'),
                   key=len, reverse=True)
_LOOKUP = r'(?:尚未|暂未|没有|未|没)(?:查询到|检索到|查到|找到)'
_FORWARD = re.compile(r'^' + _LOOKUP + r'(?P<nouns>.+)$')
_REVERSE = re.compile(
    r'^(?P<nouns>.+?)(?:：|:)?(?:均|都|也|仍)?(?:' + _LOOKUP +
    r'(?:记录|数据|资料)?|(?:查询|检索)(?:结果)?(?:为|是)?空|(?:记录)?(?:为|有)?0条)$'
)
_QUOTED = re.compile(r'“[^”]*”|「[^」]*」|"[^"]*"')
# If an earlier comma clause introduces a hypothetical, quotation/attribution
# or a denial of a claim, later clauses are not treated as standalone assertions.
_PRIOR_SCOPE = re.compile(
    r'如果|假如|假设|例如|比方|是否|若|'
    r'(?:不能|不应|不可|不得|无法).{0,8}(?:说|表述|写|断言|认为|认定|证明)|'
    r'不代表|不等于|说法|原文|居民.{0,6}(?:称|反映|表示)|记录.{0,6}(?:载明|写|称)'
)


@dataclass(frozen=True)
class SourceStateConflict:
    source_ref: str
    status: str
    clause: str


def _noun_sources(value: str) -> set[str]:
    value = re.sub(r'\s+', '', value)
    found = set()
    while value:
        word = next((word for word in _TOKENS if value.startswith(word)), None)
        if word is None:
            return set()  # Unknown syntax: abstain instead of guessing meaning.
        if word in _NOUN_SOURCES:
            found.add(_NOUN_SOURCES[word])
        value = value[len(word):]
    return found


def find_source_state_conflicts(text: str, records: dict) -> list[SourceStateConflict]:
    """Flag only recognized direct empty-result claims for unavailable sources.

    Only unscoped search:<source> keys are compared. Per-plan query scopes are
    deliberately excluded. found/empty sources, actual business truth and
    omitted/irrelevant citations are outside this check.
    """
    unavailable = {
        source: records['search:' + source]['status']
        for source in ALIASES
        if isinstance(records.get('search:' + source), dict)
        and isinstance(records['search:' + source].get('status'), str)
        and records['search:' + source].get('status') in UNAVAILABLE
    }
    result = []
    # Mask balanced quoted spans before splitting, including quoted comma lists.
    # Keep question marks: stripping them would turn questions into assertions.
    masked = _QUOTED.sub('<quoted>', text)
    for sentence in re.split(r'[。；;\n]', masked):
        prior = ''
        for original in re.split(r'[，,]', sentence):
            scoped = bool(_PRIOR_SCOPE.search(prior))
            prior += original + '，'
            if scoped:
                continue
            clause = original.strip()
            while clause:
                prefix = next((word for word in _PREFIXES if clause.startswith(word)), None)
                if prefix is None:
                    break
                clause = clause[len(prefix):].lstrip()
            match = _FORWARD.fullmatch(clause) or _REVERSE.fullmatch(clause)
            if match is None:
                continue
            for source in sorted(_noun_sources(match['nouns']) & unavailable.keys()):
                conflict = SourceStateConflict('search:' + source, unavailable[source], original.strip())
                if conflict not in result:
                    result.append(conflict)
    return result
