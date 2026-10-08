/* Baraza front-end helpers. No build step: plain ES2018, works offline. */
(function () {
  "use strict";

  var Baraza = (window.Baraza = window.Baraza || {});
  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ------------------------------------------------------------ theme */
  function storedTheme() {
    try {
      return window.localStorage.getItem("baraza-theme");
    } catch (e) {
      return null;
    }
  }
  function applyTheme(theme) {
    var root = document.documentElement;
    if (theme === "light" || theme === "dark") root.setAttribute("data-theme", theme);
    else root.removeAttribute("data-theme");
  }
  applyTheme(storedTheme());
  Baraza.toggleTheme = function () {
    var root = document.documentElement;
    var current = root.getAttribute("data-theme");
    if (!current) current = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    var next = current === "dark" ? "light" : "dark";
    applyTheme(next);
    try {
      window.localStorage.setItem("baraza-theme", next);
    } catch (e) {}
    document.dispatchEvent(new CustomEvent("baraza:theme"));
  };

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
  }
  Baraza.cssVar = cssVar;

  /* ------------------------------------------------------------ htmx CSRF */
  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    if (meta) return meta.getAttribute("content");
    var match = document.cookie.match(/csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }
  document.addEventListener("htmx:configRequest", function (evt) {
    evt.detail.headers["X-CSRFToken"] = csrfToken();
  });

  // A background job finished: reload so the page shows the new results.
  document.addEventListener("jobFinished", function () {
    setTimeout(function () {
      window.location.reload();
    }, 600);
  });

  /* ------------------------------------------------------------ helpers */
  Baraza.readJSON = function (id) {
    var el = document.getElementById(id);
    if (!el) return null;
    try {
      return JSON.parse(el.textContent);
    } catch (e) {
      return null;
    }
  };
  Baraza.stanceColor = function (v) {
    if (v === null || v === undefined) return cssVar("--stance-neutral");
    if (v > 0.2) return cssVar("--stance-support");
    if (v < -0.2) return cssVar("--stance-oppose");
    return cssVar("--stance-neutral");
  };
  Baraza.emotionColor = function (name) {
    return cssVar("--emo-" + name) || cssVar("--emo-neutral");
  };
  Baraza.pct = function (v) {
    return v === null || v === undefined ? "n/a" : Math.round(v * 100) + "%";
  };

  // Keep range inputs and their <output> in sync.
  document.addEventListener("input", function (evt) {
    var t = evt.target;
    if (t && t.matches && t.matches("input[type=range][data-output]")) {
      var out = document.getElementById(t.getAttribute("data-output"));
      if (out) out.textContent = t.getAttribute("data-percent") ? Math.round(t.value * 100) + "%" : t.value;
    }
  });

  // Modal dialogs loaded by htmx into #modal-content.
  document.addEventListener("htmx:afterSwap", function (evt) {
    if (evt.detail.target && evt.detail.target.id === "modal-content") {
      var dlg = document.getElementById("modal");
      if (dlg && !dlg.open) dlg.showModal();
    }
  });
  Baraza.closeModal = function () {
    var dlg = document.getElementById("modal");
    if (dlg) dlg.close();
  };

  /* ------------------------------------------------------------ charts */
  var registry = [];
  function baseOptions(opts) {
    opts = opts || {};
    var grid = cssVar("--chart-grid");
    var text = cssVar("--chart-text");
    var options = {
      responsive: true,
      maintainAspectRatio: false,
      animation: reduceMotion ? false : { duration: 250 },
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { position: "bottom", labels: { color: text, usePointStyle: true, boxWidth: 10, font: { family: cssVar("--font-sans") } } },
        tooltip: {
          callbacks: opts.percent
            ? {
                label: function (ctx) {
                  return ctx.dataset.label + ": " + Math.round(ctx.parsed.y * 100) + "%";
                },
              }
            : {},
        },
      },
      scales: {
        x: { ticks: { color: text, maxRotation: 0, autoSkip: true, maxTicksLimit: 8 }, grid: { color: grid } },
        y: {
          stacked: !!opts.stacked,
          ticks: { color: text },
          grid: { color: grid },
        },
      },
    };
    // Only set keys that are needed: an explicit `undefined` would override
    // Chart.js defaults (e.g. the category-axis label callback).
    var y = options.scales.y;
    if (opts.yMin !== undefined) y.min = opts.yMin;
    if (opts.yMax !== undefined) y.max = opts.yMax;
    if (opts.yTitle) y.title = { display: true, text: opts.yTitle, color: text };
    if (opts.percent) {
      y.ticks.callback = function (v) {
        return Math.round(v * 100) + "%";
      };
    }
    return options;
  }

  function makeChart(el, build) {
    if (!el || !window.Chart) return null;
    var canvas = el.tagName === "CANVAS" ? el : el.querySelector("canvas");
    if (!canvas) {
      canvas = document.createElement("canvas");
      el.appendChild(canvas);
    }
    var entry = { el: el, canvas: canvas, build: build, chart: null };
    entry.chart = new window.Chart(canvas.getContext("2d"), build());
    registry.push(entry);
    return entry;
  }
  document.addEventListener("baraza:theme", function () {
    registry.forEach(function (entry) {
      if (!document.body.contains(entry.canvas)) return;
      entry.chart.destroy();
      entry.chart = new window.Chart(entry.canvas.getContext("2d"), entry.build());
    });
  });

  /* series: [{label, data, color (css var), dash, fill}] */
  Baraza.lineChart = function (el, labels, series, opts) {
    return makeChart(el, function () {
      return {
        type: "line",
        data: {
          labels: labels,
          datasets: series.map(function (s) {
            var c = cssVar(s.color);
            return {
              label: s.label,
              data: s.data,
              borderColor: c,
              backgroundColor: s.fill ? c + "33" : c,
              borderDash: s.dash || [],
              borderWidth: s.width || 2.5,
              pointRadius: 0,
              pointHitRadius: 8,
              tension: 0.3,
              fill: s.fill || false,
              spanGaps: true,
            };
          }),
        },
        options: baseOptions(opts),
      };
    });
  };

  Baraza.stackedArea = function (el, labels, series, opts) {
    opts = Object.assign({ stacked: true }, opts || {});
    return makeChart(el, function () {
      var options = baseOptions(opts);
      options.scales.x.stacked = true;
      return {
        type: "line",
        data: {
          labels: labels,
          datasets: series.map(function (s) {
            var c = s.raw || cssVar(s.color);
            return { label: s.label, data: s.data, borderColor: c, backgroundColor: c + "B3", fill: true, pointRadius: 0, tension: 0.3, borderWidth: 1 };
          }),
        },
        options: options,
      };
    });
  };

  /* Diverging horizontal bars for segment stance (-1..1). */
  Baraza.divergingBars = function (el, labels, values, opts) {
    opts = opts || {};
    if (el && el.tagName !== "CANVAS" && labels.length > 8) {
      // One readable row per segment instead of squeezing them into a fixed box.
      el.style.height = Math.max(260, labels.length * 24 + 80) + "px";
    }
    return makeChart(el, function () {
      var options = baseOptions({});
      options.indexAxis = "y";
      options.plugins.legend.display = false;
      options.scales.x.min = -1;
      options.scales.x.max = 1;
      options.scales.x.title = { display: true, text: "Opposes  ←  0  →  Supports", color: cssVar("--chart-text") };
      options.scales.y.ticks.autoSkip = false;
      options.scales.y.ticks.callback = function (value, index) {
        var text = String(labels[index] || "");
        return text.length > 26 ? text.slice(0, 25) + "\u2026" : text; // full label stays in the tooltip
      };
      options.layout = { padding: { left: 4 } };
      options.interaction = { mode: "nearest", intersect: true };
      return {
        type: "bar",
        data: {
          labels: labels,
          datasets: [
            {
              label: opts.label || "Average stance",
              data: values,
              backgroundColor: values.map(Baraza.stanceColor),
              borderRadius: 4,
              barThickness: "flex",
              maxBarThickness: 22,
            },
          ],
        },
        options: options,
      };
    });
  };

  Baraza.histogram = function (el, series, opts) {
    var labels = [];
    for (var i = 0; i < 10; i++) {
      var lo = -1 + i * 0.2;
      labels.push(lo.toFixed(1) + " to " + (lo + 0.2).toFixed(1));
    }
    return makeChart(el, function () {
      var options = baseOptions(opts);
      options.scales.x.title = { display: true, text: "Stance (-1 strongly opposes, +1 strongly supports)", color: cssVar("--chart-text") };
      return {
        type: "bar",
        data: {
          labels: labels,
          datasets: series.map(function (s) {
            var c = cssVar(s.color);
            return { label: s.label, data: s.data, backgroundColor: s.solid ? labels.map(function (_, i) { return Baraza.stanceColor(-0.9 + i * 0.2); }) : c + "55", borderColor: c, borderWidth: s.solid ? 0 : 2, borderRadius: 3 };
          }),
        },
        options: options,
      };
    });
  };

  Baraza.bars = function (el, labels, series, opts) {
    return makeChart(el, function () {
      var options = baseOptions(opts);
      if (opts && opts.horizontal) options.indexAxis = "y";
      return {
        type: "bar",
        data: {
          labels: labels,
          datasets: series.map(function (s) {
            var c = s.raw || cssVar(s.color);
            return { label: s.label, data: s.data, backgroundColor: s.colors || c, borderRadius: 4, maxBarThickness: 28 };
          }),
        },
        options: options,
      };
    });
  };

  /* Ensemble fan chart: band between p10 and p90 with the median line. */
  Baraza.fanChart = function (el, labels, bands, opts) {
    return makeChart(el, function () {
      var datasets = [];
      bands.forEach(function (b) {
        var c = cssVar(b.color);
        datasets.push({ label: b.label + " (10th pct)", data: b.p10, borderColor: "transparent", backgroundColor: c + "26", pointRadius: 0, fill: false });
        datasets.push({ label: b.label + " (90th pct)", data: b.p90, borderColor: "transparent", backgroundColor: c + "26", pointRadius: 0, fill: "-1" });
        datasets.push({ label: b.label + " (median)", data: b.p50, borderColor: c, borderWidth: 2.5, borderDash: b.dash || [], pointRadius: 0, fill: false, tension: 0.3 });
      });
      var options = baseOptions(opts);
      options.plugins.legend.labels.filter = function (item) {
        return item.text.indexOf("median") !== -1;
      };
      return { type: "line", data: { labels: labels, datasets: datasets }, options: options };
    });
  };

  /* ------------------------------------------------------------ force graph */
  Baraza.forceGraph = function (el, data, opts) {
    if (!el || !window.d3 || !data || !data.nodes || !data.nodes.length) return;
    opts = opts || {};
    var d3 = window.d3;
    el.innerHTML = "";
    var width = el.clientWidth || 800;
    var height = el.clientHeight || 520;
    var tooltip = document.createElement("div");
    tooltip.className = "graph-tooltip";
    tooltip.setAttribute("role", "status");
    el.appendChild(tooltip);
    var svg = d3.select(el).append("svg").attr("viewBox", [0, 0, width, height]).attr("role", "img").attr("aria-label", opts.label || "Network graph");
    var g = svg.append("g");
    svg.call(
      d3
        .zoom()
        .scaleExtent([0.3, 4])
        .on("zoom", function (event) {
          g.attr("transform", event.transform);
        })
    );
    var palette = ["--emo-trust", "--emo-anger", "--emo-hope", "--emo-fear", "--emo-joy", "--emo-sadness", "--emo-disgust", "--stance-neutral"];
    var typeColors = {};
    var nodes = data.nodes.map(function (d) {
      return Object.assign({}, d);
    });
    var links = data.links.map(function (d) {
      return Object.assign({}, d);
    });
    function color(d) {
      if (opts.colorBy === "stance") return Baraza.stanceColor(d.stance);
      var key = opts.colorBy === "community" ? "c" + d.community : d.type;
      if (!(key in typeColors)) typeColors[key] = cssVar(palette[Object.keys(typeColors).length % palette.length]);
      return typeColors[key];
    }
    var radius = function (d) {
      if (d.size) return 4 + d.size * 1.6;
      return 5 + 14 * Math.sqrt(d.salience || 0.05);
    };
    var sim = d3
      .forceSimulation(nodes)
      .force("link", d3.forceLink(links).id(function (d) { return d.id; }).distance(70).strength(0.25))
      .force("charge", d3.forceManyBody().strength(-160))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("collide", d3.forceCollide().radius(function (d) { return radius(d) + 4; }));
    var link = g
      .append("g")
      .attr("stroke", cssVar("--color-border-strong"))
      .attr("stroke-opacity", 0.6)
      .selectAll("line")
      .data(links)
      .join("line")
      .attr("stroke-width", function (d) {
        return Math.min(4, 0.8 + Math.sqrt(d.weight || 1) * 0.6);
      });
    var node = g
      .append("g")
      .selectAll("g")
      .data(nodes)
      .join("g")
      .attr("tabindex", 0)
      .attr("role", "button")
      .attr("aria-label", function (d) {
        return d.label + (d.type ? " (" + d.type + ")" : "");
      })
      .call(
        d3
          .drag()
          .on("start", function (event, d) {
            if (!event.active) sim.alphaTarget(0.3).restart();
            d.fx = d.x;
            d.fy = d.y;
          })
          .on("drag", function (event, d) {
            d.fx = event.x;
            d.fy = event.y;
          })
          .on("end", function (event, d) {
            if (!event.active) sim.alphaTarget(0);
            d.fx = null;
            d.fy = null;
          })
      );
    node
      .append("circle")
      .attr("r", radius)
      .attr("fill", color)
      .attr("stroke", cssVar("--color-card"))
      .attr("stroke-width", 1.5);
    node
      .filter(function (d) {
        return (d.salience || 0) > 0.25 || (d.size || 0) > 4;
      })
      .append("text")
      .text(function (d) {
        return d.label.length > 24 ? d.label.slice(0, 23) + "…" : d.label;
      })
      .attr("x", function (d) {
        return radius(d) + 4;
      })
      .attr("y", 4)
      .attr("font-size", 11)
      .attr("fill", cssVar("--color-foreground"))
      .attr("paint-order", "stroke")
      .attr("stroke", cssVar("--color-card"))
      .attr("stroke-width", 3);
    function show(event, d) {
      var lines = ["<strong>" + escapeHtml(d.label) + "</strong>"];
      if (d.type) lines.push(escapeHtml(d.type));
      if (d.archetype) lines.push(escapeHtml(d.archetype));
      if (d.stance !== null && d.stance !== undefined) lines.push("Stance " + (d.stance > 0 ? "+" : "") + Number(d.stance).toFixed(2));
      if (d.description) lines.push(escapeHtml(d.description));
      tooltip.innerHTML = lines.join("<br>");
      var rect = el.getBoundingClientRect();
      var x = (event.clientX || rect.left + d.x) - rect.left + 12;
      var y = (event.clientY || rect.top + d.y) - rect.top + 12;
      tooltip.style.left = Math.min(x, width - 290) + "px";
      tooltip.style.top = y + "px";
      tooltip.style.opacity = 1;
    }
    node.on("mouseenter", show).on("focus", show).on("mouseleave", function () {
      tooltip.style.opacity = 0;
    }).on("blur", function () {
      tooltip.style.opacity = 0;
    });
    sim.on("tick", function () {
      link
        .attr("x1", function (d) { return d.source.x; })
        .attr("y1", function (d) { return d.source.y; })
        .attr("x2", function (d) { return d.target.x; })
        .attr("y2", function (d) { return d.target.y; });
      node.attr("transform", function (d) {
        return "translate(" + d.x + "," + d.y + ")";
      });
    });
    if (reduceMotion) {
      sim.stop();
      for (var i = 0; i < 300; i++) sim.tick();
      sim.on("tick")();
    }
    if (opts.legendEl) {
      setTimeout(function () {
        opts.legendEl.innerHTML = Object.keys(typeColors)
          .map(function (k) {
            return '<span><i style="background:' + typeColors[k] + '"></i>' + escapeHtml(k.replace(/^c(-?\d+)$/, "Community $1").replace(/_/g, " ")) + "</span>";
          })
          .join("");
      }, 50);
    }
  };

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  Baraza.escapeHtml = escapeHtml;

  /* ------------------------------------------------------------ live run */
  // Alpine component for the live simulation page.
  Baraza.liveRun = function (config) {
    return {
      status: config.status,
      round: config.round,
      total: config.total,
      metrics: null,
      timer: null,
      charts: {},
      init: function () {
        var self = this;
        this.refresh();
        if (this.isActive()) {
          this.timer = setInterval(function () {
            self.refresh();
          }, config.interval || 3000);
        }
      },
      isActive: function () {
        return this.status === "running" || this.status === "queued";
      },
      percent: function () {
        return this.total ? Math.round((100 * this.round) / this.total) : 0;
      },
      refresh: function () {
        var self = this;
        fetch(config.metricsUrl, { headers: { Accept: "application/json" } })
          .then(function (r) {
            return r.json();
          })
          .then(function (data) {
            self.status = data.status;
            self.round = data.round;
            self.total = data.total;
            self.metrics = data.latest;
            self.draw(data);
            if (!self.isActive() && self.timer) {
              clearInterval(self.timer);
              self.timer = null;
              document.dispatchEvent(new CustomEvent("baraza:run-finished"));
            }
          })
          .catch(function () {});
      },
      draw: function (data) {
        var s = data.series;
        if (!s || !s.labels) return;
        var update = function (entry, labels, sets) {
          if (!entry || !entry.chart) return false;
          entry.chart.data.labels = labels;
          sets.forEach(function (d, i) {
            if (entry.chart.data.datasets[i]) entry.chart.data.datasets[i].data = d;
          });
          entry.chart.update("none");
          return true;
        };
        if (!update(this.charts.shares, s.labels, [s.oppose, s.neutral, s.support])) {
          this.charts.shares = Baraza.stackedArea(document.getElementById("chart-shares"), s.labels, [
            { label: "Oppose", data: s.oppose, color: "--stance-oppose" },
            { label: "Undecided", data: s.neutral, color: "--stance-neutral" },
            { label: "Support", data: s.support, color: "--stance-support" },
          ], { percent: true, yMin: 0, yMax: 1 });
        }
        if (!update(this.charts.mean, s.labels, [s.mean, s.arousal])) {
          this.charts.mean = Baraza.lineChart(document.getElementById("chart-mean"), s.labels, [
            { label: "Average stance", data: s.mean, color: "--color-accent" },
            { label: "Emotional intensity", data: s.arousal, color: "--emo-anger", dash: [6, 4] },
          ], { yMin: -1, yMax: 1 });
        }
      },
    };
  };
})();
