(function () {
  "use strict";
  var canvas = document.getElementById("stars");
  var ctx = canvas.getContext("2d");
  var stars = [];
  function resize() {
    canvas.width = window.innerWidth * (window.devicePixelRatio || 1);
    canvas.height = window.innerHeight * (window.devicePixelRatio || 1);
    stars = [];
    for (var i = 0; i < 260; i++) {
      stars.push({ x: Math.random() * canvas.width, y: Math.random() * canvas.height,
        r: Math.random() * 1.6 + 0.2, t: Math.random() * Math.PI * 2, s: Math.random() * 0.02 + 0.005 });
    }
  }
  function draw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    for (var i = 0; i < stars.length; i++) {
      var star = stars[i];
      star.t += star.s;
      ctx.globalAlpha = 0.35 + 0.65 * Math.abs(Math.sin(star.t));
      ctx.fillStyle = i % 7 === 0 ? "#facc15" : "#cfe3ff";
      ctx.beginPath();
      ctx.arc(star.x, star.y, star.r, 0, Math.PI * 2);
      ctx.fill();
    }
    window.requestAnimationFrame(draw);
  }
  window.addEventListener("resize", resize);
  resize();
  draw();

  var phase = document.getElementById("phase");
  var detail = document.getElementById("detail");
  var elapsed = document.getElementById("elapsed");
  var actions = document.getElementById("actions");
  var retry = document.getElementById("retry");
  var started = Date.now();

  function invoke(cmd) {
    var core = window.__TAURI__ && window.__TAURI__.core;
    if (!core || typeof core.invoke !== "function") return Promise.reject(new Error("桌面运行时未就绪"));
    return core.invoke(cmd);
  }

  function poll() {
    elapsed.textContent = ((Date.now() - started) / 1000).toFixed(1);
    invoke("engine_boot_status").then(function (status) {
      phase.textContent = status.message || "正在启动…";
      if (status.phase === "error") {
        document.body.classList.add("error");
        actions.classList.remove("hidden");
        invoke("get_data_directory").then(function (dir) {
          if (dir) detail.textContent = "日志位置：" + dir + "/logs/muse-engine.log";
        }).catch(function () {});
      } else {
        document.body.classList.remove("error");
        actions.classList.add("hidden");
      }
    }).catch(function (error) {
      phase.textContent = "正在连接桌面运行时…";
      detail.textContent = String(error && error.message || error);
    }).finally(function () {
      window.setTimeout(poll, 150);
    });
  }

  retry.addEventListener("click", function () {
    retry.disabled = true;
    invoke("restart_engine").catch(function (error) {
      phase.textContent = "重启失败：" + (error && error.message || error);
    }).finally(function () {
      window.setTimeout(function () { retry.disabled = false; }, 2000);
    });
  });

  poll();
})();
