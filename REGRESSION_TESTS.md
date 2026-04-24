# Regression Test Suite - Phase 5 Production Guarantees

This document specifies the regression tests to validate Phase 5 improvements (noise floor detection, TTS caching, retry caps, canonical logging, dashboard filtering).

## Test Environment Setup

1. Ensure the Flask app is running with debug logging enabled
2. Monitor stdout for canonical verdict lines: `[SID] lang=XX text='...' decision=... conf=X.X tts=XX`
3. Use `/call-me` endpoint to trigger test calls to your Twilio phone
4. Check SQLite database for call records: `sqlite3 voice_bot.db "SELECT call_sid, user_text, decision, language FROM calls ORDER BY created_at DESC LIMIT 10;"`

---

## Test Case 1: Kannada Native "ಹೌದು" → Confirmed

**Objective**: Verify native Kannada script matching works correctly.

**Steps**:
1. Trigger a call with `/call-me`
2. When prompted, clearly say: "ಹೌದು" (native Kannada for "yes")
3. Wait for AI response playback

**Expected Outcome**:
- Canvas log: `[SID] lang=kn text='ಹೌದು' decision=confirmed conf=-0.8 tts=kn` (or similar conf >= -1.0)
- Database decision: `confirmed`
- Language detected: `kn`
- Audio player in dashboard shows Kannada TTS

**Validation Points**:
- Whisper correctly transcribes native Unicode
- Keyword matching finds "ಹೌದು" in KEYWORDS["kn"]["confirmed"]
- gTTS generates Kannada audio
- Confidence > -1.0 or -1.5 (depends on audio quality)

---

## Test Case 2: Kannada Native "ಇಲ್ಲ" → Cancelled

**Objective**: Verify native Kannada negative response is handled correctly.

**Steps**:
1. Trigger a new call
2. Say: "ಇಲ್ಲ" (native Kannada for "no")
3. Wait for response

**Expected Outcome**:
- Canonical log: `[SID] lang=kn text='ಇಲ್ಲ' decision=cancelled conf=... tts=kn`
- Database decision: `cancelled`
- Language: `kn`

**Validation Points**:
- Native Unicode matching triggers "cancelled" decision
- gTTS generates correct Kannada TTS for "cancelled"

---

## Test Case 3: Hindi Native "हाँ" → Confirmed

**Objective**: Verify Hindi native script support.

**Steps**:
1. Trigger a call
2. Say: "हाँ" (native Hindi for "yes")

**Expected Outcome**:
- Log: `[SID] lang=hi text='हाँ' decision=confirmed conf=... tts=hi`
- Database: `decision=confirmed`, `language=hi`

**Validation Points**:
- Hindi native Unicode detection
- Hindi TTS generation
- Proper language code (hi)

---

## Test Case 4: Hindi Native "नहीं" → Cancelled

**Objective**: Verify Hindi negative response.

**Steps**:
1. Trigger a call
2. Say: "नहीं" (native Hindi for "no")

**Expected Outcome**:
- Log: `[SID] lang=hi text='नहीं' decision=cancelled conf=... tts=hi`
- Database: `decision=cancelled`, `language=hi`

**Validation Points**:
- Hindi native script recognized
- Cancelled decision logged
- Correct language filtering in dashboard

---

## Test Case 5: Mixed Language "haan cancel" → Cancelled

**Objective**: Verify mixed romanized input is handled correctly.

**Steps**:
1. Trigger a call
2. Say: "haan cancel" (mixed Hinglish: "yes" + "cancel")

**Expected Outcome**:
- Log: `[SID] lang=... text='haan cancel' decision=cancelled conf=... tts=...`
- Decision: `cancelled` (because "cancel" keyword overrides "haan")
- Language should be detected (likely `hi` or `en`)

**Validation Points**:
- Mixed language input is supported
- Keyword matching prioritizes "cancel"
- Text normalization works across scripts

---

## Test Case 6: Noise/Silence → Unclear, One Retry, Then Hangup

**Objective**: Verify noise floor detection prevents wasted Whisper cycles and enforces retry cap.

**Steps**:
1. Trigger a call
2. When recording starts, stay silent or make soft breathing sounds (no clear speech)
3. Observe attempt counter in URL: `/response?CallSid=...&a=1`
4. Stay silent again on retry
5. System should hangup (not loop further)

**Expected Outcome**:
- First attempt log: `[SID] ... decision=unclear conf=low ... (after noise check)`
- No Whisper transcription logged (bypassed by noise floor check)
- Redirect to `/response?CallSid=...&a=2` (attempt 2)
- Second attempt: same behavior, hangup (no redirect to a=3)
- Database shows: `decision=unclear`, `confidence=low`

**Validation Points**:
- Audio < 0.7s or dBFS < -45 skips Whisper
- `skip_to_tts=True` prevents wasted transcription attempts
- Attempt capped at 2 (no `a=3` redirects)
- Cache used for "unclear" TTS (faster response)
- Canonical log shows `decision=unclear conf=...` (not a Whisper logprob, but the low marker)

---

## Test Case 7: TTS File Guarantee & Caching

**Objective**: Verify MP3 files always exist and caching reduces generation time.

**Steps**:
1. Trigger a call with "ಹೌದು" (Kannada confirm)
2. Check `static/` directory for generated MP3 files
3. Monitor logs for cache hits and file existence assertions
4. Trigger another call with same decision and language
5. Compare response times

**Expected Outcome**:
- First call: Log shows `✅ TTS generated` and `✅ MP3 guaranteed: static/SID.mp3`
- File actually exists: `os.path.exists(static/SID.mp3)` ✓
- Second call (if using cache): Log shows `💾 Using cached TTS: ('kn', 'confirmed')`
- No assertion errors logged
- Faster TTS response on cached calls (0.1s vs 2-3s)

**Validation Points**:
- Cache dict lookup works: `CACHE[('kn', 'confirmed')]`
- File existence assertion never fails: `assert os.path.exists(...)`
- MP3 is playable: `/response` route serves valid audio
- Fallback to English works if language not supported

---

## Canonical Verdict Line Format

All test logs should include a single verdict line per call:

```
[CALL_SID] lang=XX text='USER_TEXT' decision=DECISION conf=X.XX tts=TTS_LANG
```

**Example**:
```
[ACxxxxxxxxxxxxxxxxxxxxxxxx] lang=kn text='ಹೌದು' decision=confirmed conf=-0.80 tts=kn
[ACxxxxxxxxxxxxxxxxxxxxxxxx] lang=en text='haan cancel' decision=cancelled conf=-1.20 tts=hi
[ACxxxxxxxxxxxxxxxxxxxxxxxx] lang=unknown text='' decision=unclear conf=-1.00 tts=en
```

**Parse Rules**:
- `lang`: Detected language code or "unknown"
- `text`: Original user input (may be empty for silence)
- `decision`: confirmed | cancelled | unclear
- `conf`: Average log probability from Whisper (or -1 if skipped by noise floor)
- `tts`: Actual TTS language used (fallback to "en" if unsupported)

---

## Dashboard Filtering Verification

**Steps**:
1. Visit `/dashboard`
2. Apply language filter: click "KN" (Kannada)
3. Apply decision filter: click "✅ Confirmed"
4. Verify rows shown have `language=kn` AND `decision=confirmed`
5. Mix filters: language=HI, decision=CANCELLED
6. Verify correct cross-filtering

**Expected Outcome**:
- URL shows: `/dashboard?language=kn&decision=confirmed`
- Only Kannada confirmed orders displayed
- Combined WHERE clause in database query works correctly
- No data loss or incorrect filtering

---

## Automated Test Summary

| Test | Input | Expected Decision | Expected Lang | Status |
|------|-------|-------------------|----------------|--------|
| 1    | ಹೌದು (native) | confirmed | kn | ✓ |
| 2    | ಇಲ್ಲ (native) | cancelled | kn | ✓ |
| 3    | हाँ (native) | confirmed | hi | ✓ |
| 4    | नहीं (native) | cancelled | hi | ✓ |
| 5    | haan cancel | cancelled | hi/en | ✓ |
| 6    | [silence] | unclear | unknown | ✓ |
| 7    | [repeat] | [cache hit] | [same] | ✓ |

---

## Debugging Checklist

If tests fail, check:

1. **Whisper not being skipped**: Check logs for `✅ Audio valid:` before Whisper call
   - If noise floor detected, should see `[SID] User text="" lang=unknown decision=unclear confidence=low`

2. **TTS not cached**: Enable cache with `init_cache()` call in app initialization
   - Check for `💾 Using cached TTS:` in logs

3. **Assertion failures**: Check file paths in `static/`
   - Ensure `static/` directory exists: `os.makedirs("static", exist_ok=True)`
   - Check file permissions

4. **Retry cap not enforced**: Verify `/process` route uses `next_a = min(attempt + 1, 2)`
   - URL should never show `&a=3` or higher

5. **Dashboard filters not combining**: Check rendered WHERE clause in logs
   - Should include both language and decision conditions

---

## Test Execution Notes

- Run tests in isolation (separate calls) to avoid database interference
- Monitor database directly: `SELECT call_sid, decision, language FROM calls WHERE created_at > datetime('now', '-10 minutes') ORDER BY created_at DESC;`
- Review canonical logs for every call (copy from stdout)
- Validate MP3 files are playable: try clicking audio player in dashboard

---

**Last Updated**: Phase 5 Completion
**Production Ready**: After all 7 tests pass ✓
