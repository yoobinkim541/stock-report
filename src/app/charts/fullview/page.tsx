import { redirect } from 'next/navigation';

import { gatewayUrl } from '../../../lib/gateway';

export default function FullViewChartPage() {
  redirect(new URL('chart', gatewayUrl).toString());
}
