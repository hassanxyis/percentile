import { NextResponse, type NextRequest } from "next/server";

import { createClient } from "@/lib/supabase/server";

/**
 * /logout (plan §13).
 *
 * POST only. A GET would let any page log a counsellor out with an <img> tag
 * pointed here, and browsers prefetch GET links.
 */
export async function POST(request: NextRequest) {
  const supabase = await createClient();
  await supabase.auth.signOut();

  return NextResponse.redirect(new URL("/login", request.nextUrl), {
    // 303 so the browser follows with GET rather than repeating the POST.
    status: 303,
  });
}
