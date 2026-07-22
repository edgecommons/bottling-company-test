-- Compute OEE Availability from an OeeShiftSnapshot SouthboundSignalUpdate.
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
if v[1] > 0.0 then availability = v[2] / v[1] end
local s = samples[1]

return {
  signal = { id = "AvailabilityPct", name = "Availability", unit = "%" },
  samples = {{
    value = round_percent(availability),
    quality = "GOOD",
    sourceTs = s.sourceTs,
    serverTs = s.serverTs,
  }},
}
