import * as THREE from "/static/vendor/three.module.min.js";

const BACKDROP_FPS = 30;
const ORB_COUNT = 3;

const saved = window.getSettings ? window.getSettings() : {};
let prefs = { enabled: saved.backdrop3d !== false, motion: saved.motion !== false };
let backdrop = null;
let core = null;
let pointer = { x: 0, y: 0, tx: 0, ty: 0 };

window.addEventListener("pointermove", event => {
  pointer.tx = (event.clientX / window.innerWidth - 0.5) * 2;
  pointer.ty = (event.clientY / window.innerHeight - 0.5) * 2;
});

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

let glowTexture = null;
function makeGlowTexture() {
  if (glowTexture) return glowTexture;
  const size = 128;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d");
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  g.addColorStop(0, "rgba(255,255,255,0.9)");
  g.addColorStop(0.35, "rgba(255,255,255,0.28)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  glowTexture = new THREE.CanvasTexture(canvas);
  return glowTexture;
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
  scene.fog = new THREE.Fog(colors.bg, 14, 46);
  const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 90);
  camera.position.set(0, 2.5, 22);

  // Soft glowing sprites (radial-gradient texture) drifting through the scene.
  // Sprites fade to true zero alpha at their edge, so there is no hard circle silhouette like a lit sphere would show.
  const orbs = [];
  for (let i = 0; i < ORB_COUNT; i++) {
    const material = new THREE.SpriteMaterial({ map: makeGlowTexture(), transparent: true, opacity: 0.2, blending: THREE.AdditiveBlending, depthWrite: false });
    const mesh = new THREE.Sprite(material);
    const home = { x: (Math.random() - 0.5) * 26, y: 1 + Math.random() * 8, z: -6 - Math.random() * 20 };
    mesh.position.set(home.x, home.y, home.z);
    const scale = 9 + Math.random() * 8;
    mesh.scale.setScalar(scale);
    scene.add(mesh);
    orbs.push({ mesh, home, scale, drift: 0.4 + Math.random() * 0.5, phase: Math.random() * Math.PI * 2 });
  }

  const applyTheme = () => {
    const next = themeColors();
    scene.fog.color = next.bg;
    orbs.forEach((orb, i) => {
      const tone = i % 2 === 0 ? next.accent : next.muted;
      orb.mesh.material.color = tone;
    });
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
    orbs.forEach(orb => {
      orb.mesh.position.x = orb.home.x + Math.sin(elapsed * orb.drift + orb.phase) * 2.2;
      orb.mesh.position.y = orb.home.y + Math.cos(elapsed * orb.drift * 0.8 + orb.phase) * 1.4;
      const pulse = 1 + Math.sin(elapsed * 0.6 + orb.phase) * 0.06;
      orb.mesh.scale.setScalar(orb.scale * pulse);
    });
    pointer.x += (pointer.tx - pointer.x) * 0.03;
    pointer.y += (pointer.ty - pointer.y) * 0.03;
    camera.position.x = pointer.x * 3 + Math.sin(elapsed * 0.05) * 1.2;
    camera.position.y = 2.5 - pointer.y * 1.6;
    camera.lookAt(0, 0, -10);
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

// Machines without WebGL (or with it blocked) throw while creating a renderer; the panel must still work.
function safely(create) {
  try { return create(); } catch (error) { console.warn("3D scene disabled:", error.message); return null; }
}

let sceneBroken = false;

function sync() {
  if (sceneBroken) return;
  const allowed = prefs.enabled && prefs.motion;
  if (allowed && !backdrop) {
    backdrop = safely(createBackdrop);
    if (backdrop) backdrop.canvas.hidden = false;
  } else if (!allowed && backdrop) {
    disposeBackdrop();
  }
  if (!core) core = safely(createCore);
  if (!core && !backdrop && allowed) sceneBroken = true;
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
