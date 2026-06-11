export const ALL_CITIES = "全部城市";

export function buildCityOptions(dataset) {
  return [ALL_CITIES, ...dataset.cities.map((city) => city.name)];
}

export function getHourlySeries(rows = []) {
  const countsByHour = new Map(rows.map((row) => [Number(row.hour), Number(row.count)]));
  return Array.from({ length: 24 }, (_, hour) => ({
    hour,
    count: countsByHour.get(hour) ?? 0,
  }));
}

export function formatCount(value) {
  const number = Number(value) || 0;
  if (number >= 1_000_000) return `${roundToSingleDecimal(number / 1_000_000)}M`;
  if (number >= 1_000) return `${roundToSingleDecimal(number / 1_000)}k`;
  return String(number);
}

function roundToSingleDecimal(value) {
  return (Math.round((value + Number.EPSILON) * 10) / 10).toFixed(1);
}

export function buildFilteredSummary(dataset, filters) {
  const { city = ALL_CITIES, taskType = "delivery" } = filters;
  const allCities = city === ALL_CITIES;
  const cityRows = allCities
    ? dataset.cities
    : dataset.cities.filter((row) => row.name === city);
  const visibleSamples = (dataset.samples?.[taskType] ?? []).filter(
    (point) => allCities || point.city === city,
  );

  const totalTasks = allCities
    ? Number(dataset.totals?.[taskType] ?? 0)
    : cityRows.reduce((sum, row) => sum + Number(row[taskType] ?? 0), 0);
  const totalCouriers = allCities
    ? Number(dataset.totals?.couriers ?? 0)
    : cityRows.reduce((sum, row) => sum + Number(row.couriers ?? 0), 0);
  const totalAoi = cityRows.reduce((sum, row) => sum + Number(row.aoi ?? 0), 0);

  return {
    cityRows,
    visibleSamples,
    totalTasks,
    totalCouriers,
    totalAoi,
    hourlySeries: getHourlySeries(dataset.hourly?.[taskType] ?? []),
  };
}
