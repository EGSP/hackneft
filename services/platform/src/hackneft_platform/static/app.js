const chartConfig = {
  density: { chartId: "density-chart", latestId: "density-latest", color: "#2f8e70" },
  sulfur: { chartId: "sulfur-chart", latestId: "sulfur-latest", color: "#e9795b" },
};

const formatNumber = (value) => Number(value).toLocaleString("ru-RU", {
  maximumFractionDigits: 4,
});

const formatTime = (value) => new Date(value).toLocaleString("ru-RU", {
  day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
});

function drawChart(svg, records, key, color) {
  const width = 760;
  const height = 320;
  const padding = { top: 18, right: 18, bottom: 42, left: 48 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;
  svg.innerHTML = "";

  if (!records.length) {
    svg.innerHTML = `<text class="empty-state" x="${width / 2}" y="${height / 2}" text-anchor="middle">Нет данных для отображения</text>`;
    return;
  }

  const values = records.map((record) => Number(record[key]));
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const spread = maxValue - minValue || Math.max(Math.abs(maxValue) * 0.08, 1);
  const chartMin = minValue - spread * 0.12;
  const chartMax = maxValue + spread * 0.12;
  const x = (index) => padding.left + (records.length === 1 ? chartWidth / 2 : index * chartWidth / (records.length - 1));
  const y = (value) => padding.top + (chartMax - value) * chartHeight / (chartMax - chartMin);
  const points = values.map((value, index) => `${x(index)},${y(value)}`).join(" ");
  const areaPoints = `${padding.left},${padding.top + chartHeight} ${points} ${padding.left + chartWidth},${padding.top + chartHeight}`;
  const ticks = [0, 1, 2, 3, 4].map((index) => chartMin + (chartMax - chartMin) * index / 4);

  svg.innerHTML = ticks.map((value, index) => {
    const yPosition = padding.top + chartHeight - index * chartHeight / 4;
    return `<line class="grid-line" x1="${padding.left}" x2="${padding.left + chartWidth}" y1="${yPosition}" y2="${yPosition}" />
      <text class="axis-label" x="${padding.left - 10}" y="${yPosition + 4}" text-anchor="end">${formatNumber(value)}</text>`;
  }).join("");
  svg.innerHTML += `<polygon class="area" fill="${color}" points="${areaPoints}" /><polyline class="line" stroke="${color}" points="${points}" />`;
  svg.innerHTML += records.map((record, index) => `<circle class="point" fill="${color}" cx="${x(index)}" cy="${y(values[index])}" r="5"><title>${formatTime(record.timestamp)}: ${formatNumber(values[index])}</title></circle>`).join("");
  svg.innerHTML += `<text class="axis-label" x="${padding.left}" y="${height - 12}">${formatTime(records[0].timestamp)}</text>`;
  svg.innerHTML += `<text class="axis-label" x="${padding.left + chartWidth}" y="${height - 12}" text-anchor="end">${formatTime(records[records.length - 1].timestamp)}</text>`;
}

function setStatus(text, state) {
  const status = document.querySelector("#status");
  status.dataset.state = state;
  document.querySelector("#status-text").textContent = text;
}

async function loadDashboard() {
  setStatus("Загрузка данных", "loading");
  try {
    const response = await fetch("/api/pak/history");
    if (!response.ok) throw new Error("Не удалось получить данные");
    const payload = await response.json();
    const records = payload.items;
    drawChart(document.querySelector(`#${chartConfig.density.chartId}`), records, "density", chartConfig.density.color);
    drawChart(document.querySelector(`#${chartConfig.sulfur.chartId}`), records, "sulfur", chartConfig.sulfur.color);
    const latest = records[records.length - 1];
    document.querySelector("#density-latest").textContent = latest ? formatNumber(latest.density) : "--";
    document.querySelector("#sulfur-latest").textContent = latest ? formatNumber(latest.sulfur) : "--";
    document.querySelector("#record-count").textContent = `${records.length} записей`;
    setStatus("Данные актуальны", "ready");
  } catch (error) {
    setStatus(error.message, "error");
  }
}

document.querySelector("#refresh-button").addEventListener("click", loadDashboard);
loadDashboard();