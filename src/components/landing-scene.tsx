'use client';

import { useEffect, useRef } from 'react';
import * as THREE from 'three';

export function LandingScene() {
  const mountRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(36, 1, 0.1, 100);
    camera.position.set(0, 0.35, 8);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0x000000, 0);
    mount.appendChild(renderer.domElement);

    const group = new THREE.Group();
    scene.add(group);

    scene.add(new THREE.AmbientLight(0xffffff, 1.35));

    const key = new THREE.DirectionalLight(0xffffff, 2.2);
    key.position.set(4, 5, 6);
    scene.add(key);

    const rim = new THREE.PointLight(0x2278ff, 5, 12);
    rim.position.set(-3.2, 2.4, 3.2);
    scene.add(rim);

    const tablet = makePanel(4.8, 3.05, 0.18, 0xf8fbff, 0x0b1220);
    tablet.rotation.set(-0.16, -0.36, 0.08);
    tablet.position.set(0.24, 0.1, 0);
    group.add(tablet);

    const phone = makePanel(1.35, 2.7, 0.16, 0xf9fbff, 0x0b1220);
    phone.rotation.set(-0.12, -0.62, 0.05);
    phone.position.set(2.55, -0.72, 0.78);
    group.add(phone);

    const darkPad = makePanel(3.8, 2.35, 0.2, 0x0b1020, 0x111827);
    darkPad.rotation.set(-0.42, 0.28, -0.07);
    darkPad.position.set(-1.18, -1.05, -0.9);
    group.add(darkPad);

    const floor = new THREE.Mesh(
      new THREE.CircleGeometry(3.25, 64),
      new THREE.MeshBasicMaterial({ color: 0x2f80ff, transparent: true, opacity: 0.08 })
    );
    floor.rotation.x = -Math.PI / 2;
    floor.position.y = -2.18;
    group.add(floor);

    const particles = new THREE.Group();
    for (let i = 0; i < 56; i += 1) {
      const dot = new THREE.Mesh(
        new THREE.SphereGeometry(i % 7 === 0 ? 0.035 : 0.022, 12, 12),
        new THREE.MeshBasicMaterial({
          color: i % 5 === 0 ? 0x34d7c9 : i % 3 === 0 ? 0xf5b94a : 0x2563eb,
          transparent: true,
          opacity: 0.72,
        })
      );
      dot.position.set((Math.random() - 0.5) * 6.2, (Math.random() - 0.5) * 3.8, (Math.random() - 0.5) * 2.2);
      particles.add(dot);
    }
    scene.add(particles);

    let scrollProgress = 0;
    let raf = 0;

    const resize = () => {
      const rect = mount.getBoundingClientRect();
      const width = Math.max(1, rect.width);
      const height = Math.max(1, rect.height);
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    };

    const updateScroll = () => {
      const rect = mount.getBoundingClientRect();
      const viewport = window.innerHeight || 1;
      const visibleCenter = viewport / 2 - rect.top;
      scrollProgress = THREE.MathUtils.clamp(visibleCenter / (viewport + rect.height), 0, 1);
    };

    const render = (time: number) => {
      const t = time * 0.001;
      const eased = THREE.MathUtils.smoothstep(scrollProgress, 0, 1);
      group.rotation.y = -0.22 + eased * 0.72 + Math.sin(t * 0.45) * 0.035;
      group.rotation.x = -0.05 + eased * 0.18 + Math.sin(t * 0.3) * 0.025;
      group.position.y = Math.sin(t * 0.55) * 0.05 - eased * 0.1;
      tablet.position.z = Math.sin(t * 0.75) * 0.05;
      phone.position.y = -0.72 + Math.sin(t * 0.9) * 0.06 - eased * 0.18;
      darkPad.position.x = -1.18 - eased * 0.2;
      particles.rotation.y = t * 0.08;
      renderer.render(scene, camera);
      raf = window.requestAnimationFrame(render);
    };

    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    resize();
    updateScroll();
    window.addEventListener('resize', resize);
    window.addEventListener('scroll', updateScroll, { passive: true });

    if (reduceMotion) {
      renderer.render(scene, camera);
    } else {
      raf = window.requestAnimationFrame(render);
    }

    return () => {
      window.cancelAnimationFrame(raf);
      window.removeEventListener('resize', resize);
      window.removeEventListener('scroll', updateScroll);
      if (renderer.domElement.parentNode === mount) {
        mount.removeChild(renderer.domElement);
      }
      renderer.dispose();
      scene.traverse((object) => {
        if (object instanceof THREE.Mesh) {
          object.geometry.dispose();
          const material = object.material;
          if (Array.isArray(material)) {
            material.forEach((item) => item.dispose());
          } else {
            material.dispose();
          }
        }
      });
    };
  }, []);

  return <div ref={mountRef} className="lp-three" aria-label="스크롤에 반응하는 3D 대시보드 목업" />;
}

function makePanel(width: number, height: number, depth: number, face: number, edge: number) {
  const group = new THREE.Group();
  const frame = new THREE.Mesh(
    new THREE.BoxGeometry(width, height, depth),
    new THREE.MeshStandardMaterial({ color: edge, roughness: 0.58, metalness: 0.28 })
  );
  group.add(frame);

  const screen = new THREE.Mesh(
    new THREE.PlaneGeometry(width * 0.9, height * 0.82),
    new THREE.MeshStandardMaterial({ color: face, roughness: 0.44, metalness: 0.02 })
  );
  screen.position.z = depth / 2 + 0.006;
  group.add(screen);

  const blue = new THREE.Mesh(
    new THREE.BoxGeometry(width * 0.22, height * 0.08, 0.012),
    new THREE.MeshBasicMaterial({ color: 0x0d5bff })
  );
  blue.position.set(-width * 0.27, height * 0.26, depth / 2 + 0.018);
  group.add(blue);

  const cyan = new THREE.Mesh(
    new THREE.BoxGeometry(width * 0.34, height * 0.035, 0.012),
    new THREE.MeshBasicMaterial({ color: 0x34d7c9 })
  );
  cyan.position.set(width * 0.12, -height * 0.08, depth / 2 + 0.018);
  group.add(cyan);

  return group;
}
