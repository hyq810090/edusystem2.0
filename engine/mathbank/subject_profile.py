"""Per-worker subject context; standalone MathBank keeps its original defaults.

Physics rules adapted from PhysicsBank (HalHa8/physics-question-bank), AGPL-3.0.
Only trusted platform configuration selects the profile, never request text.
"""
from copy import deepcopy
import json
import re

_config = None
PHYSICS_TYPES = [
    {'value': 'single_choice', 'label': '单选题'},
    {'value': 'multi_choice', 'label': '多选题'},
    {'value': 'fill_in_blank', 'label': '填空题'},
    {'value': 'experiment', 'label': '实验题'},
    {'value': 'detailed_answer', 'label': '计算题'},
    {'value': 'short_answer', 'label': '简答题'},
]
PHYSICS_TOPICS = ('运动学', '匀变速直线运动', '牛顿运动定律', '曲线运动', '圆周运动',
    '万有引力', '机械能', '动量', '振动', '波', '光学', '静电场', '电路', '磁场',
    '电磁感应', '交变电流', '热学', '原子物理', '实验', '声', '压强', '浮力')
PHYSICS_CRITICAL = ('串联', '并联', '开路', '短路', '通电', '断电', '正极', '负极', '磁极', '电极',
    '向心', '离心', '加速', '减速', '匀速', '静止', '上升', '下降', '顺着', '逆着',
    '电流', '电压', '电阻', '电容', '电感', '电功率', '机械能', '动量', '冲量',
    '质量', '速度', '加速度', '位移', '时间', '路程', '频率', '波长', '周期', '振幅',
    '折射率', '焦距', '温度', '密度', '压强', '体积', '有效数字', '量纲',
    '受力', '合力', '摩擦力', '支持力', '拉力', '重力', '浮力', '弹力',
    '安培', '伏特', '欧姆', '牛顿', '焦耳', '瓦特', '赫兹', '特斯拉', '摄氏度')

def configure(subject='数学', stage='高中', metadata=None):
    global _config
    _config = {'subject': subject, 'stage': stage, 'metadata': deepcopy(metadata or {})}

def reset():
    global _config
    _config = None

def subject():
    return _config['subject'] if _config else '数学'

def label():
    return (_config['stage'] if _config else '高中') + subject()

def physics():
    return subject() == '物理'

def regular_paper():
    return bool(_config and (subject() != '数学' or _config['stage'] != '高中'))

def codes(key, fallback):
    values = _config['metadata'].get(key, []) if _config else []
    return ' / '.join(str(v['value']) for v in values if isinstance(v, dict) and v.get('value')) or fallback

def type_label(code, fallback):
    values = _config['metadata'].get('question_types', []) if _config else []
    return next((v['label'] for v in values if v.get('value') == code), fallback)

def classification_rules():
    if not _config:
        return ''
    data = {k: _config['metadata'].get(k, []) for k in ('question_types', 'difficulties', 'grades')}
    rule = '\n【当前学科分类配置】只使用以下配置中的 value，label 用于理解含义，不可自行新增枚举：' + json.dumps(data, ensure_ascii=False) + '\n'
    if _config['metadata'].get('difficulty_scale') == '1-5-v1':
        rule += ('难度 difficulty 用字符串 1/2/3/4/5，依次为基础、简单、中等、较难、困难，'
                 '按当前学段学科判断：直接概念或基本操作、简单应用、常规综合、多步骤复杂综合、高难推理。'
                 '证据不足用 unrated。易错、强基、竞赛、挑战只作为 tags 中逗号分隔的独立标签，'
                 '不可作为难度，也不可仅凭标签推断难度。\n')
    if physics():
        rule += '实验装置、操作、测量、数据处理与误差分析的综合题优先匹配实验题；建立方程求数值或表达式的题匹配计算题；定性解释匹配简答题。仅在相应题型已配置时使用其 value。\n'
    return rule

def preservation_rules():
    if not physics():
        return ''
    return ('\n【物理原文保真】逐项保留物理量字母与上下标、单位大小写、矢量方向、正负号、有效数字、科学记数法；'
        '不得擅自换算单位、修正原文或根据答案猜测缺损内容。新转录单位使用 \\mathrm{}，已存在的公式与单位写法保持原样。'
        '实验表格保留行列、表头单位、有效数字与小问顺序；坐标图保留轴名、单位、刻度、斜率和截距。'
        '配图包括受力图、电路图、光路图、运动图像、场线图及实验装置，保留箭头方向、电路连接、极性、元件符号和图号。'
        '来源清理只去元信息，不能删实验条件或步骤。看不清、缺图或缺公式必须标记待核对，不猜测补齐。\n')

def review_rules():
    if not physics():
        return ''
    return (preservation_rules() + '核对候选时必须逐项对照原页的单位大小写、矢量方向、有效数字、电路连接、实验装置和步骤；'
        '串联与并联、正极与负极、匀速与加速、V与mV等差异不能作为排版等价放行。证据不足保留人工核对。\n')

def solve_discipline():
    if physics():
        return (f'1. 严禁超纲：限制在中国普通{_config["stage"]}物理课程范围内，不使用大学物理或微积分；初中题不得套用高中专有规律。\n'
            '2. 规范建模：明确研究对象、物理过程、正方向和适用规律；先列原始方程再代入数值，统一矢量方向和正负号。\n'
            '3. 结果检查：数值答案带单位，保留合理有效数字；检查量纲、数量级、边界条件和物理意义。实验题说明原理、操作、数据处理与误差来源。\n')
    if _config and _config['stage'] != '高中':
        return f'1. 严禁超纲：仅使用{label()}课程内的方法和结论，不使用导数、积分或高等结论。\n'
    return '1. 严禁超纲：允许使用普通高中课程中的导数及其应用；禁止使用大学微积分方法（洛必达法则、泰勒展开、积分等）或未给定的高等结论。\n'

def drawing_rules():
    if not physics():
        return ''
    return ('\n【物理绘图】绘制受力图、电路图、光路图、运动图像和场线图时，优先保证研究对象、受力方向、导线连接、'
        '元件符号、光线方向、场线、箭头、实虚线、坐标方向及刻度正确，不得凭空改变或增加标注。'
        '只使用现有 TikZ/PGFPlots 能力，适合黑白打印。图像不清晰时不得猜测物理关系。\n')

def topics(default):
    return PHYSICS_TOPICS if physics() else default

def infer_type(prompt, metadata):
    # Only infer configured types. Explicit filters are handled by the caller.
    aliases = {'experiment': ('实验',), 'short_answer': ('简答',), 'detailed_answer': ('计算', '解答')}
    if not physics():
        return ''
    for item in metadata.get('question_types', []):
        if any(word in prompt for word in (item['label'], *aliases.get(item['value'], ()))):
            return item['value']
    return ''

def critical_pattern(default_words):
    words = (*default_words, *(PHYSICS_CRITICAL if physics() else ()))
    return (r'[0-9]+|[A-Za-z]+|[零〇一二三四五六七八九十百千万亿两壹贰叁肆伍陆柒捌玖拾佰仟萬億兆]+|'
        + '|'.join(re.escape(w) for w in sorted(set(words), key=len, reverse=True))
        + r'|[+\-=<>%‰‱°×÷±∓√∞∈∉∪∩⊂⊆⊃⊇≤≥≠≈∥⊥∠△^/():\[\]]')
