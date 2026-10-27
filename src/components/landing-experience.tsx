'use client';

import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Pause, Play } from 'lucide-react';

export function LandingExperience({ children }: { children: ReactNode }) {
  const root = useRef<HTMLDivElement>(null);
  const [paused, setPaused] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(false);

  useEffect(() => {
    const preference = window.matchMedia('(prefers-reduced-motion: reduce)');
    const syncMotion = () => setReducedMotion(preference.matches);
    syncMotion();
    preference.addEventListener('change', syncMotion);
    const elements = root.current?.querySelectorAll<HTMLElement>('[data-reveal]');
    const observer = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-visible');
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.08 });
    // Keep server-rendered content visible when JavaScript is unavailable.
    elements?.forEach(element => {
      if (element.getBoundingClientRect().top > window.innerHeight) {
        element.classList.add('reveal-ready');
        observer.observe(element);
      }
    });
    return () => {
      preference.removeEventListener('change', syncMotion);
      observer.disconnect();
    };
  }, []);

  const motionPaused = paused || reducedMotion;
  const motionLabel = reducedMotion ? '시스템 모션 감소 설정 적용됨' : paused ? '모션 재생' : '모션 일시정지';

  return <div ref={root} className="landing" data-motion={motionPaused ? 'paused' : 'running'}>
    {children}
    <button className="motion-control" type="button" disabled={reducedMotion} onClick={() => setPaused(value => !value)} aria-label={motionLabel} aria-pressed={motionPaused} title={motionLabel}>
      {motionPaused ? <Play size={13} /> : <Pause size={13} />}<span>MOTION {motionPaused ? 'OFF' : 'ON'}</span>
    </button>
  </div>;
}
