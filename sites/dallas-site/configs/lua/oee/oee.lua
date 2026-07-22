-- Compute OEE from an OeeShiftSnapshot SouthboundSignalUpdate.
-- value = { plannedProductionMs, runTimeMs, totalCount, goodCount, idealCycleMs }

local function clamp_ratio(v)
  if v < 0.0 then return 0.0 end
  if v > 1.0 then return 1.0 end
  return v
end

local function round_percent(ratio)
  return math.floor(clamp_ratio(ratio) * 1000.0 + 0.5) / 10.0
end

local function basis()
  if samples == nil or #samples == 0 or quality ~= "GOOD" then return nil end
  local v = samples[1].value
  if type(v) ~= "table" or #v < 5 then return nil end
  for i = 1, 5 do
    if type(v[i]) ~= "number" then return nil end
  end
  if v[1] < 0.0 or v[2] < 0.0 or v[3] < 0.0 or v[4] < 0.0 or v[5] <= 0.0 then
    return nil
  end
  if v[2] > v[1] or v[4] > v[3] then return nil end
  return v
end

local v = basis()
if v == nil then return nil end

local availability = 0.0
if v[1] > 0.0 then availability = clamp_ratio(v[2] / v[1]) end

local performance = 0.0
if v[2] > 0.0 then performance = clamp_ratio((v[5] * v[3]) / v[2]) end

local quality_ratio = 0.0
if v[3] > 0.0 then quality_ratio = clamp_ratio(v[4] / v[3]) end

local s = samples[1]
return {
  signal = { id = "OeePct", name = "OEE", unit = "%" },
  samples = {{
    value = round_percent(availability * performance * quality_ratio),
    quality = "GOOD",
    sourceTs = s.sourceTs,
    serverTs = s.serverTs,
  }},
}
