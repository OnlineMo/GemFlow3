from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import os
import json
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .config import get_settings
from .utils import normalize_topic


@dataclass(frozen=True)
class CandidateTopic:
    topic: str
    sources: List[str]
    edition_hint: int = 1
    confidence: float = 0.7


# 简易关键词到分类的规则（可扩展）。命中规则即返回对应分类显示名。
# 注意：此处分类名需与 settings.category_list 中的显示名一致（例如“云和 DevOps”）。
_KEYWORD_TO_CATEGORY: List[Tuple[List[str], str]] = [
    # 时政与国际
    (
        ["时政","国际","外交","关税","总统","大选","选举","欧盟","美国","美方","中方","回应","外交部","国防部",
         "阅兵","演练","天安门","港口停靠费","制裁","反制","对美","对华","公告","通告","声明"],
        "时政与国际",
    ),
    # 政策与法规
    (
        ["政策","法规","法案","条例","标准","征求意见","实施细则","监管","合规","处罚","禁令","整改",
         "国家电影局","电影局","文明办","数据安全法","隐私","gdpr","ccpa","知识产权","专利","审查","许可"],
        "政策与法规",
    ),
    # 社会与法治
    (
        ["社会","民生","警方","警察","公安","通报","判决","刑拘","逮捕","审判","法院","检察","案件","家暴",
         "猥亵","拐卖","诈骗","电诈","死亡","遇难","事故","车祸","地震","台风","大风","救援","蛇咬","咬伤",
         "受害者","嫌疑人"],
        "社会与法治",
    ),
    # 娱乐与明星
    (
        ["娱乐","明星","偶像","粉丝","恋情","官宣","绯闻","婚礼","路透","合照","营业","生图","造型","红毯",
         "热搜","塌房","出道","发文","祝福"],
        "娱乐与明星",
    ),
    # 影视综艺
    (
        ["影视","电影","电视剧","综艺","大结局","预告","定档","播出","首播","收视率","票房","首映","角色",
         "剧情","上线","下线","出演","剧组"],
        "影视综艺",
    ),
    # 音乐与演出
    (
        ["音乐","歌曲","新歌","单曲","专辑","MV","演唱会","舞台","巡演","打歌","音源","直播舞台","合唱"],
        "音乐与演出",
    ),
    # 体育与赛事
    (
        ["体育","比赛","赛事","对阵","比分","夺冠","冠军","联赛","季后赛","世界杯","奥运","中超","CBA",
         "女排","乒乓球","羽毛球","网球","法网","主场","客场","进球","捧杯"],
        "体育与赛事",
    ),
    # 游戏与电竞
    (
        ["电竞","游戏","主机","steam","unity","unreal","ue","游戏引擎","发行","玩法","账号","氪金","KPL",
         "LPL","LOL","英雄联盟","KSG","BLG","AL","王者荣耀","出海游戏","手游","端游","战队"],
        "游戏与电竞",
    ),
    # 科技与互联网
    (
        ["科技","互联网","平台","App","应用","短视频","社交媒体","算法","AI换脸","微博","抖音","快手","B站",
         "腾讯","阿里","字节","小红书","上线","优化","版本","Bug","网红","KOL","博主","账号封禁","社区"],
        "科技与互联网",
    ),
    # 人工智能和机器学习
    (
        ["人工智能","机器学习","ai","ml","深度学习","神经网络","强化学习","监督学习","无监督","automl","边缘ai",
         "计算机视觉","cv","语音识别","nlp","多模态","生成式ai","推理加速"],
        "人工智能和机器学习",
    ),
    # 大型语言模型
    (
        ["大模型","llm","语言模型","transformer","微调","对齐","rlhf","sft","rag","agent","工具调用","检索增强",
         "prompt","提示词","蒸馏","推理优化","长上下文"],
        "大型语言模型",
    ),
    # 软件开发与工程
    (
        ["软件开发","编程","工程实践","架构","设计模式","dev","ide","测试","持续集成","ci","持续交付","cd",
         "重构","代码质量","版本控制","git","微服务","单体","ddd","clean code"],
        "软件开发与工程",
    ),
    # 网络安全
    (
        ["安全","网络安全","攻击","漏洞","cve","加密","渗透","红队","蓝队","勒索","木马","钓鱼","零信任","waf",
         "防火墙","安全事件","soc","siem","入侵"],
        "网络安全",
    ),
    # 云和 DevOps
    (
        ["云","云原生","devops","kubernetes","k8s","容器","docker","helm","service mesh","istio","弹性","自动化",
         "基础设施即代码","iac","terraform","serverless","无服务器","sre","可观测性","observability","prometheus",
         "grafana"],
        "云和 DevOps",
    ),
    # 数据和数据库
    (
        ["数据","数据库","data","sql","nosql","数据湖","数据仓库","olap","oltp","etl","elt","数据治理","数据血缘",
         "数据质量","spark","flink","kafka","hadoop","流处理","批处理","向量数据库","vector db"],
        "数据和数据库",
    ),
    # 网络和移动
    (
        ["前端","web","h5","网页","移动","android","ios","小程序","flutter","react","vue","svelte","rn","pwa",
         "浏览器","端侧","跨端","适配","ui","ux"],
        "网络和移动",
    ),
    # 消费电子和硬件
    (
        ["消费电子","硬件","设备","智能手机","手机","pc","笔记本","可穿戴","ar","vr","mr","xr","耳机","相机",
         "无人机","iot","物联网","边缘设备","芯片组","soc","iPhone","安卓","屏幕","电池"],
        "消费电子和硬件",
    ),
    # 游戏与互动
    (
        ["游戏","game","主机","steam","unity","unreal","ue","游戏引擎","互动","电竞","发行","玩法","ugc","账号",
         "氪金"],
        "游戏与互动",
    ),
    # 区块链与加密
    (
        ["区块链","web3","加密","crypto","defi","nft","智能合约","solidity","以太坊","比特币","矿工","链上","钱包",
         "layer2","rollup","零知识","zk"],
        "区块链与加密",
    ),
    # 科学与太空
    (
        ["科学","科研","太空","航天","space","nasa","spacex","望远镜","火箭","卫星","物理","化学","生物","材料",
         "量子","实验","论文","arxiv"],
        "科学与太空",
    ),
    # 医疗保健与生物技术
    (
        ["医疗","健康","医疗保健","医药","药物","制药","生物","生物技术","基因","dna","rna","蛋白质","疫苗",
         "诊断","医学影像","临床","emr","电子病历"],
        "医疗保健与生物技术",
    ),
    # 能源与气候
    (
        ["能源","新能源","电力","电网","储能","电池","光伏","风电","氢能","核能","气候","碳排","碳交易","esg",
         "可持续","环保","减排","极端天气","暴雨","高温","寒潮"],
        "能源与气候",
    ),
    # 经济与市场
    (
        ["经济","宏观","通胀","利率","市场","股票","股市","债券","汇率","外汇","投资","基金","ipo","并购","融资",
         "初创","风投","vc","pe"],
        "经济与市场",
    ),
    # 财经与商业
    (
        ["财经","商业","经济","市场","股市","股票","基金","金融","汇率","外汇","价格","涨价","降价","市值","收购",
         "并购","IPO","融资","投融资","营收","财报","回收价"],
        "财经与商业",
    ),
    # 教育与考试
    (
        ["教育","学校","高校","大学","中学","老师","教师","高考","中考","考研","考公","考试","志愿","录取",
         "分数线","成绩","备考"],
        "教育与考试",
    ),
    # 旅游与出行
    (
        ["旅游","出行","游客","景区","酒店","海景房","航班","机场","延误","高铁","地铁","堵车","台风出行","自驾",
         "目的地"],
        "旅游与出行",
    ),
    # 健康与医疗
    (
        ["健康","医疗","医药","医院","医生","疾病","确诊","传染","病例","疫苗","手术","急诊","咬伤","蛇毒血清",
         "癌症","骨质疏松","体检"],
        "健康与医疗",
    ),
    # 行业与公司
    (
        ["行业","公司","企业","品牌","发布会","新品","战略","合作","裁员","业务","生态","市场份额","竞品","董秘",
         "董事长","公告","财报","营收","ipo"],
        "行业与公司",
    ),
    # 文化与媒体
    (
        ["文化","媒体","内容","影视","电影","电视剧","综艺","音乐","短视频","直播","播客","出版","新闻","记者",
         "评论","社交媒体","平台","kol","网红"],
        "文化与媒体",
    ),
]

def _default_classifier_base_url(kind: str) -> str:
    if kind == "gemini":
        return "https://generativelanguage.googleapis.com"
    if kind == "openai_compat":
        return "https://api.openai.com/v1"
    return ""


def _effective_classifier_base_url() -> tuple[str, str]:
    s = get_settings()
    kind = (getattr(s, "classifier_kind", "gemini") or "gemini").lower()
    # 1) 优先使用 settings.classifier_base_url
    base = (getattr(s, "classifier_base_url", "") or "").strip()
    # 2) 留空时回退：CLASSIFIER_BASE_URL（env）→ DEEPRESEARCH_AI_BASE_URL（env）
    if not base:
        env_base = (os.environ.get("CLASSIFIER_BASE_URL") or os.environ.get("DEEPRESEARCH_AI_BASE_URL") or "").strip()
        base = env_base
    # 3) 仍为空则按 KIND 使用官方默认
    if not base:
        base = _default_classifier_base_url(kind)
    return (base or "").rstrip("/"), kind


@retry(
    reraise=True,
    stop=stop_after_attempt(get_settings().http_max_retries),
    wait=wait_exponential(multiplier=float(get_settings().http_backoff_seconds)),
    retry=retry_if_exception_type(requests.RequestException),
)
def _invoke_classifier_service(base: str, topic: str, candidates: List[str]) -> Tuple[str, float]:
    """
    自建/旁路分类服务：POST {base}/classify
    """
    url = f"{base}/classify"
    payload: Dict[str, Any] = {"text": topic, "candidates": candidates, "language": "zh-CN"}
    resp = requests.post(url, json=payload, timeout=300)
    resp.raise_for_status()
    data = resp.json() or {}
    category = (data.get("category") or "").strip()
    try:
        confidence = float(data.get("confidence", 0.7))
    except Exception:
        confidence = 0.7
    return category, max(0.0, min(confidence, 1.0))


@retry(
    reraise=True,
    stop=stop_after_attempt(get_settings().http_max_retries),
    wait=wait_exponential(multiplier=float(get_settings().http_backoff_seconds)),
    retry=retry_if_exception_type(requests.RequestException),
)
def _invoke_classifier_gemini(base: str, model: str, token: str, topic: str, candidates: List[str]) -> Tuple[str, float]:
    """
    直接调用 Gemini 官方 API（Generative Language）：/v1beta/models/{model}:generateContent?key=...
    """
    if not token:
        return "", 0.0
    base = base or _default_classifier_base_url("gemini")
    url = f"{base}/v1beta/models/{model}:generateContent?key={token}"
    instructions = (
        "你是一个分类器。仅从给定候选集中选择一个最适合的分类，并仅返回JSON："
        '{"category":"<候选之一>","confidence":<0到1之间的小数>}。不要输出任何多余文字。'
    )
    prompt = f"{instructions}\n候选集合: {', '.join(candidates)}\n待分类文本: {topic}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0},
    }
    headers = {"Content-Type": "application/json"}
    resp = requests.post(url, headers=headers, json=payload, timeout=300)
    resp.raise_for_status()
    data = resp.json() or {}
    text = ""
    try:
        cands = data.get("candidates") or []
        if cands:
            content = cands[0].get("content") or {}
            parts = content.get("parts") or []
            if parts:
                text = parts[0].get("text") or ""
    except Exception:
        text = ""

    try:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            obj = json.loads(text[start : end + 1])
            cat = (obj.get("category") or "").strip()
            conf = float(obj.get("confidence", 0.7))
            return cat, max(0.0, min(conf, 1.0))
    except Exception:
        pass
    return "", 0.0


@retry(
    reraise=True,
    stop=stop_after_attempt(get_settings().http_max_retries),
    wait=wait_exponential(multiplier=float(get_settings().http_backoff_seconds)),
    retry=retry_if_exception_type(requests.RequestException),
)
def _invoke_classifier_openai_compat(base: str, model: str, token: str, topic: str, candidates: List[str]) -> Tuple[str, float]:
    """
    兼容 OpenAI 格式：POST {base}/chat/completions
    """
    if not token:
        return "", 0.0
    base = (base or _default_classifier_base_url("openai_compat")).rstrip("/")
    url = f"{base}/chat/completions"
    instructions = (
        "You are a classifier. Only choose one category from candidates and return pure JSON: "
        '{"category":"<one of candidates>","confidence":<float between 0 and 1>}. '
        "No extra text."
    )
    user_prompt = f"Candidates: {', '.join(candidates)}\nText: {topic}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0,
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=300)
    resp.raise_for_status()
    data = resp.json() or {}
    text = ""
    try:
        choices = data.get("choices") or []
        if choices:
            msg = choices[0].get("message") or {}
            text = msg.get("content") or ""
    except Exception:
        text = ""
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            obj = json.loads(text[start : end + 1])
            cat = (obj.get("category") or "").strip()
            conf = float(obj.get("confidence", 0.7))
            return cat, max(0.0, min(conf, 1.0))
    except Exception:
        pass
    return "", 0.0


def _ai_classify(topic: str, candidates: List[str]) -> Tuple[str, float]:
    """
    根据 classifier_kind 分派到 gemini / openai_compat / service。
    """
    s = get_settings()
    base, kind = _effective_classifier_base_url()
    model = (getattr(s, "classifier_model", None) or "").strip()
    token = (getattr(s, "classifier_token", None) or "").strip()

    if kind == "gemini":
        # 允许从环境变量回退
        token = token or os.environ.get("CLASSIFIER_TOKEN") or os.environ.get("GEMINI_API_KEY", "")
        model = model or "gemini-2.0-flash"
        return _invoke_classifier_gemini(base, model, token, topic, candidates)
    elif kind == "openai_compat":
        # 不再从 OPENAI_API_KEY 回退，只使用 CLASSIFIER_TOKEN（留空则不调用）
        token = token or os.environ.get("CLASSIFIER_TOKEN") or ""
        model = model or "gpt-4o-mini"
        return _invoke_classifier_openai_compat(base, model, token, topic, candidates)
    else:
        # 自建 service
        if not base:
            return "", 0.0
        return _invoke_classifier_service(base, topic, candidates)


def extract_candidates_via_ai(trends: List[Dict[str, Any]], top_k: int = 5) -> List[CandidateTopic]:
    """
    输入: Google 热榜 items 列表, 每项建议包含 title 与 url
    输出: 候选主题(去重与规则清洗, 保留前 top_k)
    备注: 初版使用规则提取, 后续可改为 LLM 聚合归并热点为研究主题。
    """
    seen = set()
    items: List[CandidateTopic] = []
    for item in trends:
        title = (item.get("title") or "").strip()
        url = item.get("url") or ""
        if not title:
            continue
        key = normalize_topic(title)
        if key in seen:
            continue
        seen.add(key)
        items.append(CandidateTopic(topic=title, sources=[url] if url else [], edition_hint=1, confidence=0.7))
        if len(items) >= top_k:
            break
    return items


def classify_topic(topic: str) -> Tuple[str, float]:
    """
    优先使用 AI 分类（可选），失败则回退到关键词规则与名称包含匹配。
    返回 (category_display_name, confidence)
    """
    settings = get_settings()

    # 1) AI 分类（可选）
    if getattr(settings, "classify_with_ai", False):
        candidates = list(settings.category_list or [])
        try:
            cat_ai, conf_ai = _ai_classify(topic, candidates)
            cat_ai = (cat_ai or "").strip()
            if cat_ai:
                # 归一化到候选显示名
                norm_map = {normalize_topic(c): c for c in candidates}
                mapped = norm_map.get(normalize_topic(cat_ai)) or (cat_ai if cat_ai in candidates else "")
                if mapped:
                    return mapped, conf_ai
        except Exception:
            # 忽略 AI 分类失败，走回退逻辑
            pass

    # 2) 关键词规则匹配
    text = normalize_topic(topic)
    for keywords, cat_name in _KEYWORD_TO_CATEGORY:
        for kw in keywords:
            if kw.lower() in text:
                return cat_name, 0.8

    # 3) 名称包含回退
    for cat_name in settings.category_list:
        if normalize_topic(cat_name) in text:
            return cat_name, 0.6

    # 4) 最终回退
    fallback = settings.category_list[-1] if settings.category_list else "未分类"
    return fallback, 0.5