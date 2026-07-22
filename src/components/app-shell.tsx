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
import { navItems, sidebarStats, sidebarWatchlist } from '../lib/dashboard-data';

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

      <main className="content">{children}</main>
    </div>
  );
}
