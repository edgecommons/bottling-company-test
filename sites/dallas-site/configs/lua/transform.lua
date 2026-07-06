-- transform.lua — telemetry-processor Lua "script" stage (engine = lua)
-- =====================================================================
-- WHAT THIS IS
-- The telemetry-processor runs this script ONCE PER INBOUND MESSAGE (i.e. once per
-- signal update the two filling-line adapters publish on ecv1/+/+/+/data/#). The engine
-- pre-binds a set of GLOBALS describing that one message, then calls this chunk with
-- no arguments; whatever TABLE we `return` becomes the new message `body` that is
-- streamed to the "archive" Parquet sink (return `nil` to DROP the message).
--
-- So "transform several points" means: this single script handles SEVERAL different
-- filling-line signals — OPC UA fill-head `Sine1` + `Counter`, Modbus `BottleCount`,
-- `ProductTemp`, `FillLevel` — each arrives as its own message, and for each it
-- (a) scales the raw value into engineering units, (b) REDUCES over the message's
-- `samples[]` array to derive a delta/rate, and (c) flags a threshold (overpressure /
-- overfill). The Parquet sink's `rows` projection (see telemetry-config.json) reads the
-- exact keys we emit below.
--
-- GLOBALS the engine sets for us (from telemetry-processor/docs/scripting.mdx):
--   topic        string  — source MQTT topic (ecv1/{device}/{component}/{instance}/data/{signal})
--   body         table   — the full inbound body (body.signal{ id,name,address }, body.samples[])
--   samples      array   — body.samples (1-based Lua array; each has .value, .quality, .sourceTs …)
--   value        any     — samples[1].value  (first sample's value)
--   quality      string  — samples[1].quality
--   identity     table   — SOURCE publisher's UNS identity (identity.device/component/instance/path)
--   tags         table   — envelope metadata
--   thingName / componentName / routeId — the PROCESSOR's own runtime identity
--   recvMs       integer — broker receive time (unix ms)
-- Sandbox note: os/io/require/print are removed; only string/table/math + base fns.
-- Arrays are 1-based — iterate with ipairs, length with `#`.

-- ---------------------------------------------------------------------
-- 1. Engineering-unit scaling, chosen per signal family by name.
--    Returns (engineeringValue, unitString). Non-numeric values pass through.
-- ---------------------------------------------------------------------
local function to_engineering(name, v)
  if type(v) ~= "number" then
    return v, "raw"                     -- e.g. a bool signal (JamStatus): leave as-is
  end
  if name:find("Sine") then
    -- OPC-UA fill-head sensor Sine1 swings -1..+1 -> map to fill-head pressure 50..150 kPa.
    return v * 50.0 + 100.0, "kPa"
  elseif name:find("Temp") then
    -- Modbus float32 already in degrees C from the adapter's scale/offset.
    return v, "degC"
  elseif name:find("Level") or name:find("Fill") then
    -- Fill level; the adapter already applied scale 0.1 (raw 250 -> 25.0 %).
    return v, "%"
  elseif name:find("Count") then
    return v, "count"                   -- monotonic bottle counters: pass through
  end
  return v, "raw"
end

-- ---------------------------------------------------------------------
-- 2. Drop empty reads (nothing to archive).
-- ---------------------------------------------------------------------
if samples == nil or #samples == 0 then
  return nil
end

-- ---------------------------------------------------------------------
-- 3. Resolve the signal's identity (defensive: body.signal may be absent).
-- ---------------------------------------------------------------------
local sig      = body and body.signal or {}
local sigName  = sig.name or sig.id or topic
local sigId    = sig.id or sigName
local unit     = "raw"

-- ---------------------------------------------------------------------
-- 4. Reduce over samples[] and build the transformed sample array.
--    - engValue: raw scaled into engineering units
--    - firstEng/lastEng: to derive a delta across the window
-- ---------------------------------------------------------------------
local out       = {}
local firstEng, lastEng
for i, s in ipairs(samples) do
  local eng, u = to_engineering(sigName, s.value)
  unit = u
  if type(eng) == "number" then
    if firstEng == nil then firstEng = eng end
    lastEng = eng
  end
  out[i] = {
    value    = s.value,                 -- raw value (as read from the field device)
    engValue = eng,                     -- transformed engineering value
    quality  = s.quality or "GOOD",
    sourceTs = s.sourceTs,              -- carry the field timestamp through to Parquet
  }
end

-- delta/rate across the window (0 when a single sample — the common adapter case,
-- which still demonstrates the reduction cleanly).
local rate = 0.0
if firstEng ~= nil and lastEng ~= nil then
  rate = lastEng - firstEng
end

-- ---------------------------------------------------------------------
-- 5. Threshold flag. Fill-head OVERPRESSURE (kPa > 140 — trips near the sine peaks, so
--    the `alarm` Parquet column shows a real True/False mix) or OVERFILL (level > 90 %).
-- ---------------------------------------------------------------------
local alarm = false
if type(lastEng) == "number" then
  if unit == "kPa" then
    alarm = lastEng > 140.0
  elseif unit == "%" then
    alarm = lastEng > 90.0
  end
end

-- ---------------------------------------------------------------------
-- 6. Return the new body. These keys line up 1:1 with the file-sink `rows.columns`
--    projection in telemetry-config.json (device/component/signal/unit/rate/alarm at
--    message level; value/engValue/quality/sourceTs per exploded sample).
-- ---------------------------------------------------------------------
return {
  device      = (identity and identity.device)    or thingName,
  component   = (identity and identity.component)  or componentName,
  signalId    = sigId,
  signalName  = sigName,
  unit        = unit,
  rate        = rate,
  alarm       = alarm,
  sampleCount = #samples,
  samples     = out,
}
