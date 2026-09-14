import * as THREE from "/static/vendor/three.module.min.js";

const BACKDROP_COUNT = 220;
const BACKDROP_FPS = 30;
const SPREAD = { x: 34, y: 20, z: 26 };

const saved = window.getSettings ? window.getSettings() : {};
let prefs = { enabled: saved.backdrop3d !== false, motion: saved.motion !== false };
let backdrop = null;
let core = null;

function themeColors() {
  const style = getComputedStyle(document.body);
  const read = (name, fallback) => {
    const value = style.getPropertyValue(name).trim();
    try {
      return new THREE.Color(value || fallback);
    } catch {
      return new THREE.Color(fallback);
    }
  };
  return {
    accent: read("--green", "#b9f227"),
    bg: read("--bg", "#0e1011"),
    muted: read("--muted", "#99a1a3")
  };
}

function makeRenderer(canvas) {
  const renderer = new THREE.WebGLRenderer({
    canvas, alpha: true, antialias: false, powerPreference: "low-power"
  });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
  return renderer;
}

function createBackdrop() {
  const canvas = document.getElementById("backdrop");
  if (!canvas) return null;
  const colors = themeColors();
  const renderer = makeRenderer(canvas);
  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(colors.bg, 16, 48);
  const camera = new THREE.PerspectiveCamera(55, 1, 0.1, 90);
  camera.position.set(0, 0, 26);

  scene.add(new THREE.AmbientLight(0xffffff, 0.55));
  const key = new THREE.DirectionalLight(0xffffff, 0.9);
  key.position.set(6, 10, 8);
  scene.add(key);

  const material = new THREE.MeshLambertMaterial({ transparent: true, opacity: 0.42 });
  const mesh = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), material, BACKDROP_COUNT);
  mesh.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(BACKDROP_COUNT * 3), 3);

  const cubes = [];
  const dummy = new THREE.Object3D();
  for (let i = 0; i < BACKDROP_COUNT; i++) {
    cubes.push({
      x: (Math.random() - 0.5) * SPREAD.x,
      y: (Math.random() - 0.5) * SPREAD.y,
      z: -Math.random() * SPREAD.z,
      scale: 0.28 + Math.random() * 0.85,
      spin: (Math.random() - 0.5) * 0.25,
      rise: 0.12 + Math.random() * 0.35,
      phase: Math.random() * Math.PI * 2
    });
  }
  scene.add(mesh);

  const applyTheme = () => {
    const next = themeColors();
    scene.fog.color = next.bg;
    for (let i = 0; i < BACKDROP_COUNT; i++) {
      // Mixing toward the page colour keeps distant cubes from reading as confetti.
      const shade = next.accent.clone().lerp(next.bg, 0.25 + Math.random() * 0.55);
      mesh.setColorAt(i, shade);
    }
    mesh.instanceColor.needsUpdate = true;
  };
  applyTheme();

  const resize = () => {
    const w = window.innerWidth;
    const h = window.innerHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  };
  resize();

  const draw = elapsed => {
    for (let i = 0; i < BACKDROP_COUNT; i++) {
      const c = cubes[i];
      const y = ((c.y + elapsed * c.rise + SPREAD.y / 2) % SPREAD.y) - SPREAD.y / 2;
      dummy.position.set(c.x, y, c.z);
      dummy.rotation.set(elapsed * c.spin + c.phase, elapsed * c.spin * 0.7, 0);
      dummy.scale.setScalar(c.scale);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
    }
    mesh.instanceMatrix.needsUpdate = true;
    camera.position.x = Math.sin(elapsed * 0.05) * 1.6;
    camera.position.y = Math.cos(elapsed * 0.04) * 1.1;
    camera.lookAt(0, 0, -8);
    renderer.render(scene, camera);
  };

  return { renderer, draw, resize, applyTheme, canvas };
}

function createCore() {
  const canvas = document.getElementById("server-core");
  if (!canvas) return null;
  const renderer = makeRenderer(canvas);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 40);
  camera.position.set(0, 1.5, 5.8);
  camera.lookAt(0, 0, 0);

  scene.add(new THREE.AmbientLight(0xffffff, 0.45));
  const key = new THREE.DirectionalLight(0xffffff, 0.75);
  key.position.set(3, 5, 4);
  scene.add(key);

  const group = new THREE.Group();
  const shell = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.BoxGeometry(2.4, 2.4, 2.4)),
    new THREE.LineBasicMaterial({ transparent: true, opacity: 0.55 })
  );
  const block = new THREE.Mesh(
    new THREE.BoxGeometry(1.25, 1.25, 1.25),
    new THREE.MeshLambertMaterial({ transparent: true, opacity: 0.9 })
  );
  const orbit = new THREE.Group();
  const pips = [];
  for (let i = 0; i < 10; i++) {
    const pip = new THREE.Mesh(
      new THREE.BoxGeometry(0.16, 0.16, 0.16),
      new THREE.MeshLambertMaterial()
    );
    pip.visible = false;
    pips.push(pip);
    orbit.add(pip);
  }
  group.add(shell, block, orbit);
  scene.add(group);

  const state = { running: false, players: 0 };

  const applyTheme = () => {
    const { accent, muted } = themeColors();
    const tone = state.running ? accent : muted;
    shell.material.color = tone;
    block.material.color = tone;
    block.material.emissive = state.running ? tone.clone().multiplyScalar(0.28) : new THREE.Color(0x000000);
    pips.forEach(pip => { pip.material.color = accent; });
  };

  const resize = () => {
    const w = canvas.clientWidth || 260;
    const h = canvas.clientHeight || 150;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  };

  const setState = next => {
    Object.assign(state, next);
    const shown = Math.min(state.players, pips.length);
    pips.forEach((pip, i) => {
      pip.visible = i < shown;
      if (!pip.visible) return;
      const angle = (i / Math.max(shown, 1)) * Math.PI * 2;
      pip.position.set(Math.cos(angle) * 1.7, 0, Math.sin(angle) * 1.7);
    });
    applyTheme();
  };

  const draw = elapsed => {
    const speed = state.running ? 0.35 + Math.min(state.players, 10) * 0.05 : 0.08;
    group.rotation.y = elapsed * speed;
    group.rotation.x = Math.sin(elapsed * 0.4) * 0.18;
    orbit.rotation.y = -elapsed * (speed * 1.6);
    const pulse = state.running ? 1 + Math.sin(elapsed * 2.2) * 0.04 : 1;
    block.scale.setScalar(pulse);
    renderer.render(scene, camera);
  };

  if (window.ResizeObserver) new ResizeObserver(resize).observe(canvas);

  return { renderer, draw, resize, applyTheme, setState, canvas, state };
}

let frame = null;
let lastBackdropDraw = 0;
const clock = { start: performance.now() };

function loop(now) {
  frame = requestAnimationFrame(loop);
  const elapsed = (now - clock.start) / 1000;
  if (backdrop && now - lastBackdropDraw > 1000 / BACKDROP_FPS) {
    lastBackdropDraw = now;
    backdrop.draw(elapsed);
  }
  if (core && core.canvas.offsetParent) core.draw(elapsed);
}

function running() {
  return frame !== null;
}

function start() {
  if (running() || document.hidden) return;
  frame = requestAnimationFrame(loop);
}

function stop() {
  if (frame !== null) cancelAnimationFrame(frame);
  frame = null;
}

function disposeBackdrop() {
  if (!backdrop) return;
  backdrop.renderer.dispose();
  backdrop.canvas.hidden = true;
  backdrop = null;
}

function sync() {
  const allowed = prefs.enabled && prefs.motion;
  if (allowed && !backdrop) {
    backdrop = createBackdrop();
    if (backdrop) backdrop.canvas.hidden = false;
  } else if (!allowed && backdrop) {
    disposeBackdrop();
  }
  if (!core) core = createCore();
  if (core) core.resize();
  if (backdrop || core) start(); else stop();
}

let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    backdrop?.resize();
    core?.resize();
  }, 150);
});

document.addEventListener("visibilitychange", () => {
  if (document.hidden) stop(); else start();
});

window.mcScene = {
  applyPreferences(next) {
    prefs = { ...prefs, ...next };
    sync();
    backdrop?.applyTheme();
    core?.applyTheme();
  },
  setServerState(next) {
    core?.setState(next);
  }
};

sync();
