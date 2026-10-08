"""
Sentiment and emotion scoring that works without any language model.

VADER (a rule-based, social-media-tuned sentiment model) is extended with a
small lexicon of Kenyan/East African Swahili and Sheng, Nigerian Pidgin and
South African slang that is common in African political discourse online.
Valences follow VADER's -4..+4 scale.
"""

from __future__ import annotations

import re
import threading
from collections import Counter

AFRICAN_LEXICON: dict[str, float] = {
    # --- Swahili / Sheng: positive ---
    "poa": 1.8, "safi": 2.0, "sawa": 0.8, "asante": 1.8, "hongera": 2.5, "furaha": 2.5,
    "nzuri": 2.0, "mzuri": 2.0, "bora": 1.8, "fiti": 1.8, "shwari": 1.5, "baraka": 2.0,
    "amani": 1.8, "upendo": 2.5, "pamoja": 1.2, "tunashukuru": 2.0, "nashukuru": 2.0,
    "msaada": 1.0, "haki": 1.2, "maendeleo": 1.5, "uhuru": 1.5, "tumefurahi": 2.5,
    "heko": 2.2, "shukran": 1.8,
    # --- Swahili / Sheng: negative ---
    "mbaya": -2.0, "wizi": -2.5, "mwizi": -2.5, "wezi": -2.5, "ufisadi": -2.8, "rushwa": -2.5,
    "uongo": -2.2, "mwongo": -2.2, "waongo": -2.2, "tumechoka": -2.5, "nimechoka": -2.0,
    "hasira": -2.5, "huzuni": -2.2, "shida": -1.8, "taabu": -2.0, "njaa": -2.3, "umaskini": -2.2,
    "dhuluma": -2.8, "unyanyasaji": -2.6, "aibu": -2.0, "fala": -2.5, "mjinga": -2.3,
    "ujinga": -2.0, "upuzi": -2.2, "takataka": -2.0, "ghasia": -2.2, "vurugu": -2.4,
    "ghali": -1.8, "zakayo": -1.8, "wantam": -1.5, "maandamano": -1.0, "mateso": -2.6,
    "kunyanyaswa": -2.6, "ukandamizaji": -2.8, "dhiki": -2.2, "mashaka": -1.5, "wasiwasi": -1.5,
    "kufinywa": -1.8, "tunaumia": -2.4, "tunateseka": -2.6,
    # --- Kenyan / pan-African English political vocabulary ---
    "overtaxed": -2.2, "overtaxation": -2.2, "overtaxing": -2.2, "punitive": -2.2,
    "looting": -2.8, "looters": -2.8, "looted": -2.6, "impunity": -2.5, "tribalism": -2.6,
    "goons": -2.4, "abductions": -2.8, "abducted": -2.8, "abduction": -2.8, "teargas": -1.8,
    "extortionist": -2.6, "extortionate": -2.4, "levies": -0.6, "levy": -0.4, "retrogressive": -2.0,
    "inhumane": -2.8, "unaffordable": -2.2, "unconstitutional": -2.0, "mismanagement": -2.2,
    "misappropriation": -2.4, "embezzlement": -2.6, "kleptocracy": -2.8, "pocketing": -1.8,
    "accountable": 1.5, "accountability": 1.4, "transparency": 1.5, "transparent": 1.5,
    "affordable": 1.6, "inclusive": 1.4, "empowerment": 1.8, "relief": 1.6, "subsidised": 0.8,
    "subsidized": 0.8, "uplift": 1.6, "bottom-up": 0.6,
    # --- Nigerian Pidgin ---
    "wahala": -1.8, "gbege": -1.8, "sapa": -1.8, "japa": -0.5, "katakata": -1.8,
    "ojoro": -2.2, "mumu": -2.2, "werey": -2.0, "gbas": -1.2, "sabi": 0.5, "e dey sweet": 2.0,
    # --- South African slang ---
    "eish": -1.0, "lekker": 2.0, "voetsek": -2.5, "yoh": -0.3, "sho": 0.5, "eish!": -1.0,
    "loadshedding": -2.0, "load-shedding": -2.0,
}

EMOTION_LEXICON: dict[str, set[str]] = {
    "anger": {
        "angry", "anger", "outrage", "outraged", "furious", "rage", "hate", "unacceptable", "enough",
        "shame", "shameful", "reject", "rejected", "betrayed", "betrayal", "thieves", "looting", "looters",
        "hasira", "tumechoka", "fed", "nonsense", "upuzi", "fala", "wezi", "punitive", "insult", "insulting",
        "occupy", "resist", "must", "never", "stop",
    },
    "fear": {
        "afraid", "fear", "fearful", "scared", "worried", "worry", "anxious", "anxiety", "uncertain",
        "uncertainty", "risk", "threat", "panic", "danger", "dangerous", "wasiwasi", "hofu", "mashaka",
        "abducted", "abductions", "crackdown",
    },
    "joy": {
        "happy", "glad", "great", "excellent", "love", "celebrate", "wonderful", "amazing", "finally",
        "poa", "safi", "furaha", "hongera", "lekker", "proud", "win", "victory", "thank", "thanks", "asante",
    },
    "sadness": {
        "sad", "sorry", "hopeless", "suffering", "suffer", "pain", "painful", "cry", "crying", "tears",
        "hunger", "huzuni", "njaa", "taabu", "mateso", "struggle", "struggling", "lost", "grief", "tunateseka",
    },
    "hope": {
        "hope", "hopeful", "future", "opportunity", "opportunities", "progress", "better", "improve",
        "improvement", "maendeleo", "promise", "promising", "optimistic", "growth", "jobs", "relief", "solution",
    },
    "disgust": {
        "disgusting", "disgusted", "gross", "corrupt", "corruption", "rotten", "sickening", "aibu",
        "ufisadi", "rushwa", "greed", "greedy", "shameless", "kleptocracy", "embezzlement",
    },
    "trust": {
        "trust", "confident", "confidence", "reliable", "transparent", "accountable", "believe",
        "support", "agree", "fair", "credible", "haki", "amani",
    },
}

EMOTIONS = ["anger", "fear", "sadness", "disgust", "joy", "hope", "trust", "neutral"]
HIGH_AROUSAL = {"anger": 0.9, "fear": 0.75, "disgust": 0.7, "joy": 0.6, "hope": 0.45, "sadness": 0.35, "trust": 0.3, "neutral": 0.15}

_analyzer = None
_analyzer_lock = threading.Lock()
_WORD_RE = re.compile(r"[a-zA-ZÀ-ɏ'\-]+")


def _get_analyzer():
    global _analyzer
    with _analyzer_lock:
        if _analyzer is None:
            try:
                from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
            except ImportError:  # pragma: no cover - dependency missing
                _analyzer = False
                return None
            analyzer = SentimentIntensityAnalyzer()
            analyzer.lexicon.update({k: v for k, v in AFRICAN_LEXICON.items() if " " not in k})
            _analyzer = analyzer
        return _analyzer or None


def sentiment(text: str) -> float:
    """Compound sentiment in [-1, 1]."""
    if not text or not text.strip():
        return 0.0
    analyzer = _get_analyzer()
    if analyzer is not None:
        return float(analyzer.polarity_scores(text[:5000])["compound"])
    # Fallback: tiny lexicon average.
    words = [w.lower() for w in _WORD_RE.findall(text)]
    scores = [AFRICAN_LEXICON[w] for w in words if w in AFRICAN_LEXICON]
    if not scores:
        return 0.0
    total = sum(scores)
    return max(-1.0, min(1.0, total / (abs(total) + 4.0) * 1.5))


def emotion(text: str) -> tuple[str, float]:
    """Return (dominant emotion, arousal 0..1) using a keyword lexicon."""
    if not text:
        return "neutral", HIGH_AROUSAL["neutral"]
    words = [w.lower().strip("'-") for w in _WORD_RE.findall(text)]
    counts: Counter = Counter()
    for word in words:
        for label, vocab in EMOTION_LEXICON.items():
            if word in vocab:
                counts[label] += 1
    exclaim = text.count("!")
    caps = sum(1 for w in text.split() if len(w) > 3 and w.isupper())
    if not counts:
        label = "neutral"
    else:
        label = counts.most_common(1)[0][0]
    arousal = HIGH_AROUSAL[label] + min(0.25, 0.05 * exclaim + 0.05 * caps)
    return label, round(min(1.0, arousal), 3)


def sentiment_label(score: float) -> str:
    if score >= 0.25:
        return "positive"
    if score <= -0.25:
        return "negative"
    return "neutral"
