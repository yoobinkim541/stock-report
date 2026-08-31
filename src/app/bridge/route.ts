import { NextResponse } from 'next/server';

import { gatewayUrl } from '../../lib/gateway';

export function GET() {
  return NextResponse.redirect(gatewayUrl, 307);
}
