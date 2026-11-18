import type { Metadata } from 'next';
import localFont from 'next/font/local';
import './globals.css';

const landingFont = localFont({
  src: '../../public/fonts/stock-report-sans.woff2',
  display: 'swap',
  weight: '45 920',
  variable: '--font-landing',
});

export const metadata: Metadata = {
  title: 'Stock Report — Trading Intelligence',
  description: '시장을 읽는 시야, 판단을 바꾸는 차이. 미국·한국 주식 차트, 출처 기반 AI 리서치, 전략 검증과 포트폴리오 리스크 관리를 하나의 워크스페이스에서 연결하세요.',
  openGraph: {
    title: 'Stock Report — Trading Intelligence',
    description: '차트, AI 리서치, 전략 검증. 당신의 다음 판단에 더 선명한 근거를.',
    locale: 'ko_KR',
    type: 'website',
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko" className={landingFont.variable}>
      <body>{children}</body>
    </html>
  );
}
