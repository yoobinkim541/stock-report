'use client';

import type { ReactNode } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  ArrowRight,
  BookOpen,
  Bot,
  Clock3,
  Database,
  FileText,
  Layers3,
  Search,
  Sparkles,
  TrendingUp,
} from 'lucide-react';
import { navItems, pageMeta, sidebarStats, sidebarWatchlist } from '../lib/dashboard-data';

type ShellProps = {
  children: ReactNode;
};

const iconMap = {
  home: Sparkles,
  portfolio: Database,
  analysis: Search,
  charts: Layers3,
  'market-calendar': Clock3,
  'mock-invest': TrendingUp,
  research: FileText,
  'ai-console': Bot,
  'ai-wiki': BookOpen,
} as const;

export function AppShell({ children }: ShellProps) {
  const pathname = usePathname();
  const activeItem = navItems.find((item) => pathname === item.href || (item.href !== '/' && pathname.startsWith(item.href))) ?? navItems[0];
  const meta = pageMeta[activeItem.key];

  const pageSignals = [
    { label: 'active route', value: activeItem.label },
    { label: 'memory layer', value: 'World Memory' },
    { label: 'reading mode', value: meta.kicker },
  ];

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-top">
            <div className="brand-badge">
              <span className="badge-dot" />
              stock-report agent
            </div>
            <span className="status-chip teal">ready</span>
          </div>
          <div>
            <h1>Stock Report</h1>
            <p>홈, 포트폴리오, 종목 분석, 차트, 캘린더, 모의투자, 리서치, AI 콘솔, AI 위키를 한 곳에 묶습니다.</p>
          </div>
        </div>

        <nav className="sidebar-nav" aria-label="주요 섹션">
          {navItems.map((item) => {
            const active = pathname === item.href || (item.href !== '/' && pathname.startsWith(item.href));
            const Icon = iconMap[item.key];

            return (
              <Link key={item.key} href={item.href} className={`nav-item ${active ? 'active' : ''}`}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, textAlign: 'left' }}>
                  <Icon size={16} />
                  <div>
                    <strong>{item.label}</strong>
                    <div>
                      <span>{item.href}</span>
                    </div>
                  </div>
                </div>
                <ArrowRight size={15} />
              </Link>
            );
          })}
        </nav>

        <section className="sidebar-section">
          <h2>현재 상태</h2>
          <div className="source-list">
            {sidebarStats.map((item) => (
              <div className="source-item" key={item.label}>
                <div className="source-item-top">
                  <span className="source-name">{item.label}</span>
                  <span className="source-count">live</span>
                </div>
                <p>{item.value}</p>
                <p>{item.detail}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="sidebar-section">
          <h2>보유 종목</h2>
          <div className="source-list">
            {sidebarWatchlist.map((ticker) => (
              <div className="source-item" key={ticker.symbol}>
                <div className="source-item-top">
                  <span className="source-name">{ticker.symbol}</span>
                  <span className={`source-count ${ticker.tone}`}>{ticker.delta}</span>
                </div>
                <p>{ticker.name}</p>
                <p>{ticker.price}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="sidebar-footer">
          <strong>레이어 분리</strong>
          <p>화면은 React, 수집과 동기화는 Python으로 분리하면 덜 무겁고 유지보수도 쉬워집니다.</p>
        </section>
      </aside>

      <main className="content">
        <div style={{ display: 'grid', gap: 16 }}>
          <header
            className="page-top"
            style={{
              display: 'grid',
              gridTemplateColumns: 'minmax(0, 1fr) 320px',
              gap: 16,
              alignItems: 'stretch',
              padding: 22,
              borderRadius: 26,
              border: '1px solid rgba(148, 163, 184, 0.16)',
              background: 'linear-gradient(135deg, rgba(13, 18, 31, 0.96), rgba(9, 13, 24, 0.84))',
              boxShadow: '0 30px 90px rgba(0, 0, 0, 0.28)',
            }}
          >
            <div>
              <div
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 8,
                  padding: '6px 10px',
                  borderRadius: 999,
                  border: '1px solid rgba(52, 215, 201, 0.18)',
                  background: 'rgba(52, 215, 201, 0.08)',
                  color: 'var(--muted)',
                  fontSize: 12,
                  marginBottom: 12,
                }}
              >
                stock-report / {meta.kicker}
              </div>
              <h2 style={{ margin: 0, fontSize: 32, lineHeight: 1.05, letterSpacing: 0 }}>{meta.title}</h2>
              <p style={{ margin: '10px 0 0', maxWidth: 760, color: 'var(--muted)', lineHeight: 1.62, fontSize: 15 }}>
                {meta.subtitle}
              </p>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 16 }}>
                <span className="status-chip teal">market data</span>
                <span className="status-chip violet">World Memory</span>
                <span className="status-chip amber">graph layer</span>
                <span className="status-chip rose">react shell</span>
              </div>
            </div>

            <div
              style={{
                display: 'grid',
                gap: 10,
                alignContent: 'start',
                padding: 14,
                borderRadius: 20,
                background: 'rgba(7, 11, 20, 0.62)',
                border: '1px solid rgba(148, 163, 184, 0.12)',
              }}
            >
              {pageSignals.map((signal) => (
                <div
                  key={signal.label}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: 12,
                    padding: '10px 12px',
                    borderRadius: 14,
                    background: 'rgba(15, 22, 36, 0.78)',
                    border: '1px solid rgba(148, 163, 184, 0.1)',
                  }}
                >
                  <span style={{ color: 'var(--muted)', fontSize: 12, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                    {signal.label}
                  </span>
                  <strong style={{ fontSize: 13, textAlign: 'right' }}>{signal.value}</strong>
                </div>
              ))}
            </div>
          </header>

          <section
            style={{
              display: 'grid',
              gridTemplateColumns: 'minmax(0, 1.25fr) minmax(320px, 0.75fr)',
              gap: 16,
              padding: 18,
              borderRadius: 24,
              border: '1px solid rgba(148, 163, 184, 0.14)',
              background: 'linear-gradient(180deg, rgba(14, 20, 34, 0.86), rgba(10, 15, 26, 0.72))',
              boxShadow: 'var(--shadow)',
            }}
          >
            <div style={{ minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 12 }}>
                <strong style={{ fontSize: 13, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                  active lens
                </strong>
                <span className="status-chip teal">{activeItem.label}</span>
              </div>
              <p style={{ margin: 0, color: 'var(--text)', fontSize: 16, lineHeight: 1.7 }}>
                지금 화면은 <strong style={{ color: 'white' }}>{activeItem.label}</strong>에 맞춰 정렬되어 있습니다. 왼쪽에서 맥락을, 가운데에서 핵심 판단을, 오른쪽에서 실행 레일을 읽는 구조예요.
              </p>
            </div>

            <div style={{ display: 'grid', gap: 10 }}>
              <div
                style={{
                  padding: 12,
                  borderRadius: 16,
                  border: '1px solid rgba(52, 215, 201, 0.14)',
                  background: 'rgba(7, 11, 20, 0.64)',
                }}
              >
                <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 4 }}>read path</div>
                <strong>{meta.kicker}</strong>
              </div>
              <div
                style={{
                  padding: 12,
                  borderRadius: 16,
                  border: '1px solid rgba(167, 139, 250, 0.14)',
                  background: 'rgba(7, 11, 20, 0.64)',
                }}
              >
                <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 4 }}>context scope</div>
                <strong>market · memory · execution</strong>
              </div>
            </div>
          </section>

          {children}
        </div>
      </main>
    </div>
  );
}
