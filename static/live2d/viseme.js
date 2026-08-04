"use strict";

/*
 * YUMENO viseme lip-sync core.
 *
 * Ported from BandoriPet's text→viseme model (tts_manager.py):
 *   - estimateVisemeUnits(): text → phoneme-class sequence (7 classes)
 *   - allocatePoses():      spread units across audio duration
 *   - computeMouth():       blend viseme pose with real-time audio energy
 *
 * Compared to BandoriPet's original constants, the pose table is amplified
 * (~50% larger open values, wider form range) so the mouth motion is clearly
 * visible even at moderate volume.
 *
 * Public API:
 *   PLViseme.detectLanguage(text)
 *   PLViseme.estimateVisemeUnits(text, language?)
 *   PLViseme.allocatePoses(units, durationSec, language?)
 *   PLViseme.computeMouth(level, baseOpen, baseForm)
 */
window.PLViseme = (function () {
  // (open, form) per phoneme class — amplified variant of BandoriPet's table.
  const VISEME_POSES = {
    aa: { open: 0.85, form: 0.0 },
    ow: { open: 0.55, form: -0.85 },
    iy: { open: 0.45, form: 0.9 },
    uw: { open: 0.38, form: -1.0 },
    mbp: { open: 0.0, form: 0.0 },
    fv: { open: 0.3, form: 0.45 },
    neutral: { open: 0.35, form: 0.0 },
  };

  const WORD_RE = /^[A-Za-z']+$/;
  const KANA_RE = /^[\u3040-\u30ff]+$/;
  const CJK_RE = /[\u3400-\u9fff]/;
  const PUNCT_RE = /[，,。.！!？?：:；;、~～\-—…]/;

  const KANA_MBP = "まみむめもマミムメモばびぶべぼバビブベボぱぴぷぺぽパピプペポんン";
  const KANA_FV = "ふフゔヴ";
  const KANA_IY = "いきぎしじちぢにひびぴみりイキギシジチヂニヒビピミリぇェゃャ";
  const KANA_UW = "うくぐすずつづぬぶぷむゆるウクグスズツヅヌブプムユルぅゥゅュ";
  const KANA_OW = "おこごそぞとのほぼぽもよろをオコゴソゾトノホボポモヨロヲぉォょョ";
  const KANA_AA = "ぁァあかがさざただなはばぱやらわアカガサザタダナハバパヤラワ";

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function detectLanguage(text) {
    const t = String(text || "");
    if (/[\u3040-\u30ff]/.test(t)) return "ja";
    if (CJK_RE.test(t)) return "zh";
    if (/[A-Za-z]/.test(t)) return "en";
    return "";
  }

  function unitsPerSecond(language) {
    const lang = String(language || "").toLowerCase();
    if (lang === "en" || lang === "english" || lang === "英文") return 10;
    if (lang === "ja" || lang === "japanese" || lang === "日文") return 8;
    return 7;
  }

  function estimateLatinWordVisemes(word) {
    const units = [];
    let i = 0;
    while (i < word.length) {
      const pair3 = word.slice(i, i + 3);
      const pair2 = word.slice(i, i + 2);
      const ch = word[i];
      if (pair2 === "mb" || pair2 === "mp") {
        units.push("mbp");
        i += 2;
        continue;
      }
      if (ch === "m" || ch === "b" || ch === "p") {
        units.push("mbp");
      } else if (ch === "f" || ch === "v") {
        units.push("fv");
      } else if (pair3 === "you" || pair3 === "yoo" || pair2 === "oo" || pair2 === "uu" || pair2 === "ew") {
        units.push("uw");
        i += pair3 === "you" || pair3 === "yoo" ? 3 : 2;
        continue;
      } else if (pair2 === "ow" || pair2 === "oh" || pair2 === "oa" || pair2 === "ou" || pair2 === "aw" || pair2 === "au") {
        units.push("ow");
        i += 2;
        continue;
      } else if (pair2 === "ee" || pair2 === "ea" || pair2 === "ie" || ch === "i" || ch === "y") {
        units.push("iy");
      } else if (ch === "u") {
        units.push("uw");
      } else if (ch === "o") {
        units.push("ow");
      } else if (ch === "a" || ch === "e") {
        units.push("aa");
      } else if (ch === "r" || ch === "l" || ch === "w" || ch === "n") {
        units.push("neutral");
      } else {
        units.push("aa");
      }
      i += 1;
    }
    return units;
  }

  function estimateKanaVisemes(text) {
    const units = [];
    for (const ch of text) {
      if (KANA_MBP.includes(ch)) units.push("mbp");
      else if (KANA_FV.includes(ch)) units.push("fv");
      else if (KANA_IY.includes(ch)) units.push("iy");
      else if (KANA_UW.includes(ch)) units.push("uw");
      else if (KANA_OW.includes(ch)) units.push("ow");
      else if (KANA_AA.includes(ch)) units.push("aa");
      else units.push("neutral");
    }
    return units;
  }

  function estimateVisemeUnits(text, language) {
    const t = String(text || "").trim();
    if (!t) return [];
    const lang = String(language || detectLanguage(t)).toLowerCase();
    const units = [];
    const tokens = t.match(/[A-Za-z']+|[\u3040-\u30ff]+|[\u3400-\u9fff]+|./g) || [];
    for (const token of tokens) {
      if (!token || /^\s$/.test(token)) continue;
      if (WORD_RE.test(token)) {
        units.push(...estimateLatinWordVisemes(token.toLowerCase()));
      } else if (KANA_RE.test(token)) {
        units.push(...estimateKanaVisemes(token));
      } else if (CJK_RE.test(token)) {
        const fallback = lang === "zh" || lang === "chinese" || lang === "中文" ? "aa" : "neutral";
        for (let i = 0; i < token.length; i += 1) units.push(fallback);
      } else if (PUNCT_RE.test(token)) {
        units.push("mbp");
      } else {
        units.push("neutral");
      }
    }
    return units;
  }

  function allocatePoses(units, durationSec, language) {
    const duration = Number(durationSec) || 0;
    if (duration <= 0 || !units.length) return [];
    const rate = unitsPerSecond(language);
    const count = Math.max(1, Math.round(duration * rate));
    const sequence = [];
    for (let i = 0; i < count; i += 1) {
      const name = units[i] || "neutral";
      sequence.push({ end: (duration * (i + 1)) / count, ...VISEME_POSES[name] });
    }
    return sequence;
  }

  function probePose(poses, timeSec) {
    const t = Number(timeSec) || 0;
    for (const pose of poses) {
      if (t < pose.end) return pose;
    }
    return poses[poses.length - 1] || VISEME_POSES.neutral;
  }

  // Amplified blend: voice energy drives the open amount, the viseme class
  // supplies the mouth shape (form) and floors the open value.
  function computeMouth(level, baseOpen, baseForm) {
    if (baseOpen < 0.05) {
      return { open: Math.min(level * 0.2, 0.04), form: 0.0 };
    }
    const activity = level > 0.01 ? clamp(level / 0.18, 0, 1) : 0;
    const open = clamp(Math.max(level * 1.15, baseOpen * activity), 0, 0.9);
    const form = clamp(baseForm * Math.max(0.25, activity), -1, 1);
    return { open, form };
  }

  return {
    detectLanguage,
    estimateVisemeUnits,
    allocatePoses,
    probePose,
    computeMouth,
    poses: VISEME_POSES,
  };
})();
