import { redirect } from 'next/navigation';

import { gatewayUrl } from '../../lib/gateway';

export default function BridgePage() {
  redirect(gatewayUrl);
}
