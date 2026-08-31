import { redirect } from 'next/navigation';

import { gatewayUrl } from '../../lib/gateway';

export default function PortfolioPage() {
  redirect(new URL('portfolio', gatewayUrl).toString());
}
