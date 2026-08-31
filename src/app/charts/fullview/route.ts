import { NextResponse } from 'next/server';

import { gatewayUrl } from '../../../lib/gateway';

export function GET() {
  return NextResponse.redirect(new URL('chart', gatewayUrl), 307);
}
