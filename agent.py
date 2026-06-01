# -*- coding: utf-8 -*-
"""agent.py — AI sales engine (Alex)."""

import sqlite3, json, time, random, threading, re
from datetime import datetime

from config  import DB, groq_chat, send_message, _log, _meta_lock
from prompts import SYSTEM_PROMPT


# ── Language word sets ────────────────────────────────────────────────
_PUNJABI_WORDS = {
    "kida","kidda","kiven","karda","karde","kardi","tusi","tuc","tuhade","tuhada","tuhadi","veere",
    "hunda","hundi","lagda","lagdi","reha","rehi","dasso","dassa","vadiya","vadia","vdia","vdiya",
    "changa","changi","mainu","sanu","tenu","aunde","jaande","kithe","kiddan","kadon","agge","dhandha",
    "ni","yr","yar","yaar","kuj","hale","kenda","kende","paye","pya","gall","gal","haigi","va","ta",
    "dssna","paaji","paji","veera","bai","english ni","angrezi ni",
}
_HINDI_WORDS = {
    "kaise","karo","karte","karta","aapke","aapka","hoga","nahi","batao","achha","theek hai",
    "koi baat nahi","karenge","hoon","rahaan","bataiye","boliye","samjho","chahiye",
    "aap","kya","hai","mein","raha","rahi","kaun","kon","bol","rha","rhe",
}

def _detect_language(text: str) -> str:
    words = set(re.sub(r"[^\w\s]", "", text.lower()).split())
    tl = text.lower()
    if words & {"tusi","tuc","tuhade","tuhada","tuhadi","mainu","sanu","tenu","mai"}:
        return "punjabi"
    pb = len(words & _PUNJABI_WORDS) + ("theek aa" in tl) + ("koi gal ni" in tl)
    hi = len(words & _HINDI_WORDS) + ("theek hai" in tl) + ("koi baat nahi" in tl)
    if pb == 0 and hi == 0: return "english"
    if pb > hi: return "punjabi"
    if hi > pb: return "hindi"
    return "english"

_EXPLICIT_PUNJABI = re.compile(
    r'\b(pa?njabi\s*(ch|vich|wich|bol|gal)|mainu\s*(english|angrezi)\s*ni|'
    r'english\s*(ni\s*aundi|nahi\s*aati|nahi\s*aundi)|roman\s*punjabi)\b', re.I)
_EXPLICIT_HINDI   = re.compile(r'\b(hindi(\s*(mein|me|bol))?|mujhe\s*english\s*nahi|hindustani)\b', re.I)
_EXPLICIT_ENGLISH = re.compile(r'\b(in\s*english|speak\s*english|english\s*(bol|mein))\b', re.I)
_OFFTOPIC         = re.compile(
    r'\b(england|america|usa|uk|canada|australia|europe|paris|london|dubai|travel|trip|visit|'
    r'vacation|holiday|flight|hotel|tourism|movie|film|netflix|game|cricket|football|sport|'
    r'song|music|dance|food|eat|cook|recipe)\b', re.I)
_FILLER_ONLY      = re.compile(
    r'^[\s\W]*(ok|okay|hmm|hm|ha|haha|lol|nice|sure|cool|great|thanks|thank\s*you|ty|👍|😊|😄|🙏)+[\s\W]*$', re.I)
_CLARIFICATION    = re.compile(
    r'^[\s\W]*(ki|kya|what|huh)(\s+(bro|bhai|paaji|paji|veer|veere|yr|yar|yaar))?[\s\W]*$'
    r'|(?:\bki\b.*\b(kenda|kende|paye|paya|jo|va)\b)|(?:\bwhat\b.*\b(saying|mean)\b)', re.I)
_IDENTITY         = re.compile(
    r'^\s*(?:nai|nahi|ni|sir|bro|bhai|paaji|paji|veer|yr|yaar|yar|\s)*\s*(kon|kaun|who)\s*(?:ji|jo|hai|ho|are\s+you|is\s+this)?\s*\??\s*$'
    r'|^\s*(?:tusi|tuc|tu|aap|tum|you)\s+(kon|kaun|who)\s*(?:ji|jo|ho|hai|are)?\s*\??\s*$'
    r'|\b(kon|kaun)\s+(bol|baat)\s+(rha|raha|rhe|rahe)\s+(hai|ho)\b'
    r'|\b(who\s+are\s+you|who\s+is\s+this|who\s+am\s+i\s+talking\s+to)\b', re.I)
_ABUSIVE          = re.compile(r'\b(idiot|stupid|pagal|mad|dumb|nonsense|bakwas|faltu|shut\s*up)\b', re.I)
_DISENGAGE        = re.compile(
    r'\b(meri\s+mrzi|nai\s+dssna|nahi\s+dssna|ni\s+dssna|not\s+gon+a\s+tell|not\s+gonna\s+tell|'
    r'not\s+going\s+to\s+tell|not\s+telling|wont\s+tell|won\'t\s+tell|rehndo|rehn\s+do|'
    r'ja\s+prawa|ja\s+pra|bye|ok\s+bye|stop|leave\s+me)\b', re.I)
_LANG_COMPLAINT   = re.compile(
    r'\b(mixing|mix)\b.*\b(punjabi|hindi|english)\b|\b(punjabi|hindi|english)\b.*\b(mixing|mix)\b', re.I)
_PRIVACY          = re.compile(
    r'^\s*why\s*\??\s*$|\bwhy\s+(do|should|would)\s+i\s+(tell|share)\b|'
    r'\bwhy\s+(you|u)\s+(ask|asking)\b|\bkiun\s+dassa\b|\bkiu\s+dssa\b', re.I)
_OOS              = re.compile(
    r'\b(weather|cricket|movie|song|recipe|politics|news|game|travel|flight|hotel|'
    r'medicine|medical|health|relationship)\b', re.I)
_MEDICAL          = re.compile(
    r'\b(stomach|pet|dard|pain|ache|sick|vomit|fever|bukhar|tabiyat|health|doctor|medicine|medical)\b', re.I)
_STUDENT          = re.compile(r'\b(student|padhai|study|college|school|ai/ml|ai\s*ml)\b', re.I)
_BUSINESS_HINT    = re.compile(
    r'\b(business|dhandha|kamm|kaam|real\s*estate|property|shop|store|agency|company|'
    r'startup|freelance|clients?|customers?|grahak|leads?)\b', re.I)
_REFERRAL         = re.compile(r'\b(referrals?|reference|word.of.mouth|portal|portals?)\b', re.I)
_HARD_EXIT        = re.compile(r'\b(bye|ok\s+bye|ja\s+prawa|ja\s+pra|stop|leave\s+me)\b', re.I)
_REAL_ESTATE_TERMS = ("real estate", "property", "realtor", "broker")
_MEDICAL_ADVICE   = re.compile(
    r'\b(remedies?|ginger|antacid|painkillers?|ibuprofen|paracetamol|heat\s*pack|ice\s*pack|'
    r'pain\s*reliever|physician|tablet|medicine|treatment|diagnos\w*|symptoms?)\b', re.I)
_SHORT_HOSTILE    = re.compile(r'\b(you|idiot|stupid|mad|dumb|nonsense|shut\s*up|bye|stop|leave\s+me)\b', re.I)
_CLEAR_ENG        = {"i","you","your","we","our","that","this","the","an","to","for","with","and",
                     "or","but","know","get","more","clients","customers","leads","business","company",
                     "how","what","why","when","where","can","do","does","is","are","want","need",
                     "understand","think","have","has"}
_PB_CHAT_BLOCKERS = {"ni","yr","yar","yaar","aa","rhe","reha","rehi","paye","kende","da","de","di",
                     "te","ch","mera","mainu"}


# ── Language persistence ──────────────────────────────────────────────
def _save_language_lock(conv_id: str, lang: str):
    with sqlite3.connect(DB) as conn:
        conn.execute("UPDATE conversations SET language_lock=? WHERE id=?", (lang, conv_id))
        conn.commit()

def _is_clear_english_switch(text: str) -> bool:
    words = re.sub(r"[^\w\s]", " ", text.lower()).split()
    if len(words) < 3: return False
    if set(words) & _PUNJABI_WORDS or set(words) & _HINDI_WORDS or set(words) & _PB_CHAT_BLOCKERS:
        return False
    return sum(1 for w in words if w in _CLEAR_ENG) >= 2

def _get_conversation_language(conv_id: str) -> str:
    with sqlite3.connect(DB) as conn:
        row   = conn.execute("SELECT language_lock FROM conversations WHERE id=?", (conv_id,)).fetchone()
        saved = (row[0] or "").strip() if row else ""
        recent = conn.execute(
            "SELECT content FROM messages WHERE conversation_id=? AND role='user' ORDER BY id DESC LIMIT 5",
            (conv_id,)).fetchall()
        first  = conn.execute(
            "SELECT content FROM messages WHERE conversation_id=? AND role='user' ORDER BY id ASC LIMIT 1",
            (conv_id,)).fetchone()

    if not recent: return "english"

    for (c,) in recent:
        if _EXPLICIT_PUNJABI.search(c): _save_language_lock(conv_id, "punjabi"); return "punjabi"
        if _EXPLICIT_HINDI.search(c):   _save_language_lock(conv_id, "hindi");   return "hindi"
        if _EXPLICIT_ENGLISH.search(c): _save_language_lock(conv_id, "english"); return "english"

    meaningful = [(c,) for (c,) in recent if not _FILLER_ONLY.match(c)]
    if meaningful:
        lang = _detect_language(meaningful[0][0])
        if lang in ("punjabi", "hindi"): _save_language_lock(conv_id, lang); return lang
        if saved in ("punjabi", "hindi") and _is_clear_english_switch(meaningful[0][0]):
            _save_language_lock(conv_id, "english"); return "english"

    if saved in ("punjabi", "hindi"): return saved

    pool  = meaningful[:3] if meaningful else recent[:3]
    votes = {"english": 0, "punjabi": 0, "hindi": 0}
    for (c,) in pool: votes[_detect_language(c)] += 1
    best = max(votes, key=votes.get)
    if votes[best] >= 2: _save_language_lock(conv_id, best); return best

    if first:
        lang = _detect_language(first[0])
        if lang != "english": _save_language_lock(conv_id, lang)
        return lang
    return "english"

def _lang_for_sensitive(conv_id: str, msg: str) -> str:
    return "english" if _SHORT_HOSTILE.search(msg) else _get_conversation_language(conv_id)


# ── Sanitizers ────────────────────────────────────────────────────────
def _make_sanitizer(pairs):
    compiled = [(re.compile(r'(?i)\b' + re.escape(p) + r'\b'), r) for p, r in pairs]
    def _run(text):
        for pat, repl in compiled:
            def _rep(m, r=repl):
                return (r[0].upper() + r[1:]) if r and m.group(0)[0].isupper() else r
            text = pat.sub(_rep, text)
        return re.sub(r' {2,}', ' ', text).strip()
    return _run

_sanitize_english = _make_sanitizer([
    ("koi gal ni","no worries"),("koi baat nahi","no worries"),("theek aa","alright"),
    ("vadiya","nice"),("changa","nice"),("changi","great"),("veere","bro"),
    ("tuhada","your"),("tuhadi","your"),("tuhade","your"),("tusi","you"),
    ("dasso","tell me"),("dass","tell me"),("pra","bro"),
    ("hun",""),("reha",""),("karda",""),("hunda",""),("lagda",""),
])
_sanitize_punjabi = _make_sanitizer([
    ("you","tusi"),("i","main"),("we","asi"),("us","sanu"),("me","mainu"),
    ("your","tuhada"),("my","mera"),("our","sada"),("is","aa"),("are","ne"),
    ("was","si"),("were","si"),("has","aa"),("have","aa"),("will","karange"),
    ("can","sakde"),("should","chahida"),("would","karange"),("what","ki"),
    ("why","kiun"),("how","kidaan"),("when","kado"),("where","kithe"),
    ("business","dhandha"),("customer","grahak"),("help","madad"),
    ("good","vadia"),("great","vadia"),("nice","vadia"),("okay","theek aa"),
    ("ok","theek aa"),("yes","haan ji"),("no","ni"),("sorry","maafi"),
    ("thanks","shukriya"),("please","meharbaani"),
])
_sanitize_hindi = _make_sanitizer([
    ("you","aap"),("i","main"),("we","hum"),("me","mujhe"),
    ("your","aapka"),("my","mera"),("our","hamara"),("is","hai"),("are","hain"),
    ("was","tha"),("were","the"),("will","karenge"),("can","sakte"),
    ("what","kya"),("why","kyun"),("how","kaise"),("when","kab"),("where","kahan"),
    ("business","dhandha"),("help","madad"),("good","accha"),("great","accha"),
    ("yes","haan"),("no","nahi"),("sorry","maafi"),
])

_SANITIZERS = {"english": _sanitize_english, "punjabi": _sanitize_punjabi, "hindi": _sanitize_hindi}

_LANG_INSTRUCTION = {
        "english": (
        "LANGUAGE LOCK — ENGLISH ONLY. Zero Punjabi/Hindi words.\n"
        "FORBIDDEN: vadiya, changa, theek aa, veere, tusi, tuhade, koi gal ni, hunda, reha, karda.\n"
        "Replace: 'Vadiya'→'Nice', 'Theek aa'→'Alright'. Keep replies under 22 words, 1 sentence by default."
    ),
    "punjabi": (
        "LANGUAGE LOCK — PURE PUNJABI ONLY (Roman script). Think like a native Punjabi speaker.\n"
        "ALLOWED: tusi, tuhade, karda, reha, hunda, vadiya, changa, theek aa, koi gal ni, veere, ch, naal, layi.\n"
        "FORBIDDEN (rewrite if any appear): tu(alone), tera, teri, hai, nahi, kya, aap, aapke, achha, theek hai,\n"
        "  raha, karte, karta, hoon, mein, zaroorat(→lodd), baat(→gal), paisa kamai(→kamaai).\n"
        "Keep replies under 22 words, 1 sentence by default."
    ),
    "hindi": (
        "LANGUAGE LOCK — PURE HINDI ONLY (Roman script). Think like a native Hindi speaker.\n"
        "ALLOWED: aap, kya, hai, nahi, karte, achha, raha, karenge, chahiye.\n"
        "FORBIDDEN: tusi, tuhade, aa(=is), ni(=no), karda, reha, vadiya, changa, koi gal ni, dasso.\n"
        "Keep replies under 22 words, 1 sentence by default."
    ),
}


# ── Guardrail replies (deterministic, no LLM) ─────────────────────────
def _r(lang, pb, hi, en):
    """Return the right string for the detected language."""
    return pb if lang == "punjabi" else (hi if lang == "hindi" else en)

def guardrail_reply(conv_id: str, last_msg: str, recent_user_msgs: list, occupation: str = ""):
    """Return (reply_text, switch_manual) or None if no guardrail matches."""
    tail = (recent_user_msgs or [])[-6:]
    hostile_n  = sum(1 for m in tail if _ABUSIVE.search(m or ""))
    disengage_n = sum(1 for m in tail if _DISENGAGE.search(m or ""))
    lang = _get_conversation_language(conv_id)

    if _MEDICAL.search(last_msg):
        return (_r(lang,
            "ohho paaji, take care. eh medical gal aa, doctor naal gal karni vadia rahegi. baad ch gal karange.",
            "arre bhai, take care. yeh medical baat hai, doctor se baat karna better rahega. baad mein baat karenge.",
            "Oh no, take care. That is medical, so it is better to check with a doctor. We can talk later."), False)

    if _STUDENT.search(last_msg) and not _BUSINESS_HINT.search(last_msg):
        return (_r(lang,
            "sahi paaji. main zyada kamm/dhandhe ch online grahak laun ch madad karda haan. tuhade kol koi kamm ya plan aa?",
            "sahi bhai. main zyada kaam/dhandhe mein online customers laane mein help karta hoon. aapke paas koi kaam ya plan hai?",
            "Nice. I mainly help businesses get customers online. Do you have any work or business plan?"), False)

    if _REFERRAL.search(last_msg):
        return (_r(lang,
            "referrals vadia ne, par slow hunde ne. Meta ads naal nave buyers direct aa sakde ne.",
            "referrals acche hote hain, par slow hote hain. Meta ads se naye buyers direct aa sakte hain.",
            "Referrals are good, but slow. Meta ads can bring fresh buyers directly."), False)

    if _IDENTITY.search(last_msg):
        return (_r(lang,
            "main Alex haan, ViralIQ company ch sales agent. main online grahak laun ch madad karda haan.",
            "main Alex hoon, ViralIQ company mein sales agent. online marketing aur naye customers laane mein help karta hoon. aap kis kaam mein ho?",
            "I'm Alex, a sales agent at ViralIQ. I help businesses get more customers through online marketing. What kind of work are you in?"), False)

    if _LANG_COMPLAINT.search(last_msg):
        _save_language_lock(conv_id, "english")
        return ("You're right, I mixed the languages. I'll match whatever language you use from here.", False)

    if _PRIVACY.search(last_msg):
        return (_r(lang,
            "fair aa paaji. main sirf eh samajhan layi puchheya si ki marketing di gal tuhade layi relevant aa ya ni. je share ni karna, koi pressure ni.",
            "fair hai bhai. main sirf yeh samajhne ke liye pooch raha tha ki marketing wali baat relevant hai ya nahi. share nahi karna to koi pressure nahi.",
            "Fair question. I only ask so I know whether marketing advice is relevant for you. No pressure."), False)

    if _ABUSIVE.search(last_msg):
        lang = _lang_for_sensitive(conv_id, last_msg)
        if hostile_n >= 2:
            return (_r(lang,
                "samajh gaya paaji, main hor push ni karanga. jadon sach ch madad chahidi hove, ek message kar dena.",
                "samajh gaya bhai, main ab push nahi karunga. jab sach mein help chahiye ho, ek message kar dena.",
                "I get it. I won't push further. If you need help later, just message me."), True)
        return (_r(lang,
            "lagda main zyada push kar reha haan paaji. main simple rakhda haan: je madad chahidi aa tan dass dena, nahi tan koi pressure ni.",
            "lagta hai main zyada push kar raha hoon bhai. simple rakhta hoon: help chahiye ho to bata dena, warna koi pressure nahi.",
            "Looks like I pushed too much. If you want help, tell me where you're stuck. No pressure."), False)

    if _DISENGAGE.search(last_msg):
        lang = _lang_for_sensitive(conv_id, last_msg)
        hard = disengage_n >= 2 or _HARD_EXIT.search(last_msg)
        if hard:
            return (_r(lang,
                "theek aa paaji, main rukda haan. jadon lodd hove message kar dena.",
                "theek hai bhai, main yahin rukta hoon. jab zaroorat ho message kar dena.",
                "Got it, I'll stop here. Message me later if you need anything."), True)
        return (_r(lang,
            "bilkul paaji, tuhadi marzi. main hor sawaal ni puchhda; bas jadon madad chahidi hove dass dena.",
            "bilkul bhai, aapki marzi. main aur sawaal nahi puchunga; jab help chahiye ho bata dena.",
            "Fair enough. I won't ask more questions; just message me if you want help later."), False)

    if _OOS.search(last_msg):
        return (_r(lang,
            "main sirf kamm te online grahak vali gal ch madad karda haan. tuhade kamm ch sab ton vaddi dikkat ki aa?",
            "main sirf business aur online customers wali baat mein help karta hoon. aapke kaam mein sabse badi dikkat kya hai?",
            "I only help with business and getting customers online. What's the biggest issue in your business right now?"), False)

    if _CLARIFICATION.search(last_msg):
        cust_text = " ".join(recent_user_msgs or []).lower()
        occ = occupation.strip().lower()
        if not occ and any(t in cust_text for t in _REAL_ESTATE_TERMS): occ = "real estate"
        is_re = any(t in occ for t in _REAL_ESTATE_TERMS)
        if lang == "punjabi":
            msg = ("maafi paaji, mera matlab eh aa: property de kamm ch zyada dikkat nave grahak laun ch aa ya sahi kharidar labhan ch?" if is_re
                   else "maafi paaji, main eh keh reha si: main online grahak laun ch madad karda haan. je lodd ni, koi pressure ni.")
        elif lang == "hindi":
            msg = ("maafi bhai, mera matlab yeh hai: real estate mein zyada dikkat leads laane mein hai ya serious buyers dhoondhne mein?" if is_re
                   else "maafi bhai, mera matlab yeh hai: main online customers laane mein madad karta hoon. zaroorat nahi hai to koi pressure nahi.")
        else:
            msg = ("Sorry bro, I meant: in real estate, is the bigger issue getting enough leads or finding serious buyers?" if is_re
                   else "Sorry bro, I meant I help with online marketing and getting customers. No pressure if that's not useful.")
        return (msg, False)

    return None


# ── Occupation detection ──────────────────────────────────────────────
def detect_and_save_occupation(conv_id):
    with sqlite3.connect(DB) as conn:
        row = conn.execute("SELECT occupation FROM conversations WHERE id=?", (conv_id,)).fetchone()
        if row and row[0]: return row[0]
        msgs = conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id=? ORDER BY id ASC", (conv_id,)).fetchall()

    if len(msgs) < 4: return ""
    convo = "\n".join(f"{'Alex' if r[0]=='assistant' else 'Customer'}: {r[1]}" for r in msgs)
    try:
        resp = groq_chat(messages=[
            {"role": "system", "content": "Detect business/occupation from a sales conversation. Return ONLY 2-5 words (e.g. 'restaurant owner', 'real estate agent'). If unclear, return: unknown"},
            {"role": "user", "content": convo}
        ], temperature=0.1, timeout=20)
        detected = (resp.choices[0].message.content or "").strip().lower()
        if detected and detected != "unknown" and len(detected) < 60:
            with sqlite3.connect(DB) as conn:
                conn.execute("UPDATE conversations SET occupation=? WHERE id=?", (detected, conv_id))
                conn.commit()
            return detected
    except Exception as e:
        print(f"Occupation detection error: {e}")
    return ""


# ── Interest analysis ─────────────────────────────────────────────────
def analyze_interest(conv_id):
    with sqlite3.connect(DB) as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id=? ORDER BY id ASC", (conv_id,)).fetchall()
    if not any(r[0] == "user" for r in rows): return None

    convo_text = "\n".join(f"{'ALEX' if r[0]=='assistant' else 'CUSTOMER'}: {r[1]}" for r in rows)
    user_msgs  = [r[1] for r in rows if r[0] == "user"]
    combined   = " ".join(user_msgs).lower()
    kw_list    = [
        "how","what","why","when","which","how much","what if","can you","do you","is it",
        "ads","advertising","facebook ads","instagram ads","google ads","how does it work",
        "tell me more","what do you do","price","cost","charges","fees","plans","packages",
        "budget","results","roi","leads","sales","customers","bookings","grow","growth",
        "interested","want to try","let's do it","sign up","start","let's go",
        "audit","free","trial","demo","call","meeting","next step",
    ]
    hits = [k for k in kw_list if k in combined]

    prompt = f"""Analyze this sales conversation and return ONLY valid JSON.

CONVERSATION:
{convo_text}

CUSTOMER MESSAGE COUNT: {len(user_msgs)}
INTEREST KEYWORDS FOUND: {hits if hits else "none"}
KEYWORD COUNT: {len(hits)}

Return:
{{"level":"High"|"Medium"|"Low"|"None","score":<0-100>,"signals":["..."],"recommendation":"..."}}

Scoring:
None(0-9): not responding/clearly not interested.
Low(10-35): just started, short replies.
Medium(36-65): sharing problems, asking questions.
High(66-100): asking price/cost/packages OR wants to start/book.
Rules: price/packages mentioned = instantly High(68+). Questions = at least Medium(45+).
Messages 1-2: max score 25. Keyword hits={len(hits)}."""

    try:
        resp = groq_chat(messages=[
            {"role": "system", "content": "Sales analysis AI. Return only valid JSON, no markdown."},
            {"role": "user", "content": prompt}
        ], temperature=0.1, timeout=25)
        raw = (resp.choices[0].message.content or "").strip()
        if not raw: return None
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"): raw = raw[4:]
            raw = raw.strip().rstrip("```").strip()
        s, e = raw.find("{"), raw.rfind("}") + 1
        if s == -1 or e <= s: return None
        data  = json.loads(raw[s:e])
        level = str(data.get("level", "Low"))
        try: score = max(0, min(100, int(float(str(data.get("score", 0))))))
        except: score = 0
        with sqlite3.connect(DB) as conn:
            conn.execute("""UPDATE conversations
                SET interest_level=?, interest_score=?, interest_signals=?, recommendation=?, updated_at=?
                WHERE id=?""",
                (level, score, json.dumps(data.get("signals", []) or []),
                 str(data.get("recommendation","") or ""),
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S"), conv_id))
            conn.commit()
        return data
    except Exception as e:
        print(f"Analysis error: {e}")
        return None


# ── Per-conversation locks + timers ───────────────────────────────────
_conv_locks  = {}
_conv_timers = {}

def _get_lock(conv_id):
    with _meta_lock:
        if conv_id not in _conv_locks:
            _conv_locks[conv_id] = threading.Lock()
        return _conv_locks[conv_id]


# ── Time-waster messages ──────────────────────────────────────────────
_TW_EXITS = [
    "honestly been great chatting but i think this might not be the right time for you — no hard feelings, take care 👋",
    "seems like this probably isn't for you right now — totally fine, all the best 😊",
    "haha i think we're going in circles tbh — i'll leave you to it, take care 👋",
    "no stress at all — doesn't seem like the right fit right now, best of luck with everything 😊",
]
_TW_NUDGES = [
    "haha just checking — are you actually looking to grow your business or just curious? no judgment either way 😄",
    "tbh i want to make sure i'm actually useful to you — what's the main thing you're trying to figure out?",
    "genuine question — is there something specific you're looking for or just exploring?",
]


# ── Core reply loop ───────────────────────────────────────────────────
def process_and_reply(conv_id, from_number):
    lock = _get_lock(conv_id)
    if not lock.acquire(blocking=False):
        _log(f"[{conv_id[:6]}] Locked — retry in 6s")
        t = threading.Timer(6.0, process_and_reply, args=(conv_id, from_number))
        with _meta_lock: _conv_timers[conv_id] = t
        t.start()
        return

    try:
        # ── Load state ───────────────────────────────────────────────
        with sqlite3.connect(DB) as conn:
            row = conn.execute(
                "SELECT occupation, last_processed_msg_id FROM conversations WHERE id=?", (conv_id,)).fetchone()
            if not row: _log(f"[{conv_id[:6]}] Conversation not found"); return
            occupation, last_proc_id = row[0] or "", row[1] or 0

            pending = conn.execute(
                "SELECT id, content FROM messages WHERE conversation_id=? AND role='user' AND id > ? ORDER BY id ASC",
                (conv_id, last_proc_id)).fetchall()
            if not pending: _log(f"[{conv_id[:6]}] Nothing pending"); return

            _raw = conn.execute(
                "SELECT role, content FROM messages WHERE conversation_id=? AND id < ? ORDER BY id DESC LIMIT 40",
                (conv_id, pending[0][0])).fetchall()
            history = list(reversed(_raw))

            sr = conn.execute("SELECT interest_score, interest_level FROM conversations WHERE id=?", (conv_id,)).fetchone()
            interest_score = int(sr[0] or 0) if sr else 0
            interest_level = (sr[1] or "New") if sr else "New"
            total_msgs = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id=?", (conv_id,)).fetchone()[0]

        # ── Helper: save + send ──────────────────────────────────────
        def _send(msg, switch_manual=False):
            ts  = datetime.now().strftime("%I:%M %p")
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            lid = pending[-1][0]
            with sqlite3.connect(DB) as conn:
                conn.execute(
                    "INSERT INTO messages (conversation_id, role, content, timestamp) VALUES (?, 'assistant', ?, ?)",
                    (conv_id, msg, ts))
                if switch_manual:
                    conn.execute("UPDATE conversations SET updated_at=?, last_processed_msg_id=?, chat_mode='manual' WHERE id=?",
                                 (now, lid, conv_id))
                else:
                    conn.execute("UPDATE conversations SET updated_at=?, last_processed_msg_id=? WHERE id=?",
                                 (now, lid, conv_id))
                conn.commit()
            send_message(from_number, msg)

        last_msg        = pending[-1][1]
        recent_user_msgs = [c for _, c in history if _ == "user"] + [c for _, c in pending]

        # ── Guardrails ───────────────────────────────────────────────
        result = guardrail_reply(conv_id, last_msg, recent_user_msgs, occupation)
        if result:
            msg, switch = result
            _send(msg, switch_manual=switch)
            _log(f"[{conv_id[:6]}] Guardrail handled")
            return

        # ── Time-waster filter ───────────────────────────────────────
        if total_msgs >= 12 and interest_score < 25 and interest_level not in ("High","Medium"):
            _send(random.choice(_TW_EXITS), switch_manual=True)
            _log(f"[{conv_id[:6]}] Time-waster exit (score={interest_score})")
            return
        if total_msgs >= 8 and 0 < interest_score < 25 and interest_level not in ("High","Medium"):
            _send(random.choice(_TW_NUDGES))
            _log(f"[{conv_id[:6]}] Low-interest nudge (score={interest_score})")
            return

        # ── Off-topic redirect ───────────────────────────────────────
        if _OFFTOPIC.search(last_msg):
            lang = _get_conversation_language(conv_id)
            _send(_r(lang,
                "main sirf business te grahak vali gal ch madad karda haan. tuhade kamm ch lead di dikkat aa?",
                "main sirf business aur customers wali baat mein help karta hoon. aapko leads ki dikkat hai?",
                "I only help with business and customer growth. Are leads the main issue for you?"))
            _log(f"[{conv_id[:6]}] Off-topic redirect")
            return

        # ── Build messages for LLM ───────────────────────────────────
        lang      = _get_conversation_language(conv_id)
        lang_lock = _LANG_INSTRUCTION.get(lang, _LANG_INSTRUCTION["english"])
        _log(f"[{conv_id[:6]}] Language: {lang}")

        combined_input = (pending[0][1] if len(pending) == 1
                          else "\n".join(f"[msg {i+1}] {m[1]}" for i, m in enumerate(pending)))
        if len(pending) > 1: _log(f"[{conv_id[:6]}] Batching {len(pending)} messages")

        system_prompt = SYSTEM_PROMPT
        if occupation:
            system_prompt += f"\n\nKnown customer business/occupation: {occupation}. Use this only as context; do not invent details."
        messages = [{"role": "system", "content": system_prompt + "\n\n" + lang_lock}]
        for role, content in history:
            messages.append({"role": "user" if role == "user" else "assistant", "content": content})
        messages.append({"role": "system", "content": f"[REMINDER] {lang_lock}"})
        messages.append({"role": "user", "content": combined_input})

        # ── LLM call ─────────────────────────────────────────────────
        reply = None
        try:
            resp  = groq_chat(messages=messages, temperature=0.7, top_p=0.9, max_tokens=45, timeout=30)
            reply = (resp.choices[0].message.content or "").strip()
            reply = _SANITIZERS.get(lang, _sanitize_english)(reply)
            if _MEDICAL_ADVICE.search(reply):
                reply = _r(lang,
                    "eh mera area ni aa. sehat wali gal layi doctor naal gal karni vadia rahegi. baad ch gal karange.",
                    "yeh mera area nahi hai. health wali baat ke liye doctor se baat karna better rahega. baad mein baat karenge.",
                    "That is not my area. For health stuff, better to check with a doctor. We can talk later.")
                _log(f"[{conv_id[:6]}] Medical advice blocked")
        except Exception as e:
            _log(f"[{conv_id[:6]}] Groq error: {e}")

        if not reply: _log(f"[{conv_id[:6]}] No reply generated"); return

        time.sleep(random.uniform(2, 3))

        # ── Save + send ──────────────────────────────────────────────
        ts  = datetime.now().strftime("%I:%M %p")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with sqlite3.connect(DB) as conn:
                conn.execute(
                    "INSERT INTO messages (conversation_id, role, content, timestamp) VALUES (?, 'assistant', ?, ?)",
                    (conv_id, reply, ts))
                conn.execute("UPDATE conversations SET updated_at=?, last_processed_msg_id=? WHERE id=?",
                             (now, pending[-1][0], conv_id))
                conn.commit()
        except Exception as e:
            _log(f"[{conv_id[:6]}] DB save error: {e}"); return

        send_message(from_number, reply)
        _log(f"[{conv_id[:6]}] Replied ({len(pending)} msg(s))")

        try:
            if not occupation: detect_and_save_occupation(conv_id)
            analyze_interest(conv_id)
        except Exception as e:
            _log(f"[{conv_id[:6]}] Post-reply analysis error: {e}")

    finally:
        lock.release()
        try:
            with sqlite3.connect(DB) as conn:
                chk = conn.execute("SELECT last_processed_msg_id FROM conversations WHERE id=?", (conv_id,)).fetchone()
                if chk:
                    still = conn.execute(
                        "SELECT COUNT(*) FROM messages WHERE conversation_id=? AND role='user' AND id > ?",
                        (conv_id, chk[0])).fetchone()[0]
                    if still > 0:
                        _log(f"[{conv_id[:6]}] {still} msg(s) arrived — re-queuing")
                        schedule_reply(conv_id, from_number)
        except Exception:
            pass


# ── Debounce wrapper ──────────────────────────────────────────────────
def schedule_reply(conv_id, from_number):
    """Batch rapid messages: wait 3s after last message, then reply once."""
    with _meta_lock:
        if conv_id in _conv_timers and _conv_timers[conv_id]:
            _conv_timers[conv_id].cancel()
        t = threading.Timer(3.0, process_and_reply, args=(conv_id, from_number))
        _conv_timers[conv_id] = t
    t.start()
