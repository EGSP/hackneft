const SULFUR_THRESHOLD = 10;
const SULFUR_SENSOR_NAME = "sulfur";
const SULFUR_COLOR = "#e9795b";

const formatNumber = (value) => Number(value).toLocaleString("ru-RU", {
  maximumFractionDigits: 4,
});

const formatTime = (value) => new Date(value).toLocaleString("ru-RU", {
  day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
});

let sulfurChart = null;

function renderChart(canvas, records) {
  const labels = records.map((record) => formatTime(record.timestamp));
  const values = records.map((record) => Number(record.value));

  if (sulfurChart) {
    sulfurChart.data.labels = labels;
    sulfurChart.data.datasets[0].data = values;
    sulfurChart.update();
    return;
  }

  sulfurChart = new Chart(canvas, {
    type: "line",
    data: {
      labels,
      datasets: [{
        label: "Сера",
        data: values,
        borderColor: SULFUR_COLOR,
        backgroundColor: `${SULFUR_COLOR}26`,
        fill: true,
        tension: 0.3,
        pointRadius: 4,
        pointHoverRadius: 6,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "nearest", axis: "x", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (context) => `Сера: ${formatNumber(context.parsed.y)}`,
          },
        },
        annotation: {
          annotations: {
            thresholdLine: {
              type: "line",
              scaleID: "y",
              value: SULFUR_THRESHOLD,
              borderColor: "#c0392b",
              borderWidth: 2,
              borderDash: [6, 6],
              label: {
                display: true,
                content: `Граница: ${SULFUR_THRESHOLD}`,
                position: "end",
                backgroundColor: "#c0392b",
                color: "white",
                font: { size: 11 },
              },
            },
          },
        },
      },
      scales: {
        y: { beginAtZero: false },
      },
    },
  });
}

function setStatus(text, state) {
  const status = document.querySelector("#status");
  status.dataset.state = state;
  document.querySelector("#status-text").textContent = text;
}

async function loadDashboard() {
  setStatus("Загрузка данных", "loading");
  try {
    const response = await fetch("/api/sensor-data/history");
    if (!response.ok) throw new Error("Не удалось получить данные");
    const payload = await response.json();
    const records = payload.items.filter(
      (item) => item.sensor_name.trim().toLowerCase() === SULFUR_SENSOR_NAME
    );

    renderChart(document.querySelector("#sulfur-chart"), records);

    const latest = records[records.length - 1];
    document.querySelector("#sulfur-latest").textContent = latest ? formatNumber(latest.value) : "--";
    document.querySelector("#record-count").textContent = `${records.length} записей`;
    setStatus("Данные актуальны", "ready");
  } catch (error) {
    setStatus(error.message, "error");
  }
}

document.querySelector("#refresh-button").addEventListener("click", loadDashboard);
loadDashboard();
