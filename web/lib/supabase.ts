import { createClient, type SupabaseClient } from "@supabase/supabase-js";

let browserClient: SupabaseClient | null = null;

// A lazily-created singleton rather than a module-level `createClient()`
// call — that would run (and throw on missing env vars) during `next
// build`'s static generation of the benchmark homepage, which must have
// zero backend or auth dependency (phase-13-frontend.md's "Homepage —
// statically rendered"). There is no Next.js server-side route that ever
// needs the session (the API is a separate FastAPI service reached only
// from the browser with a Bearer token), so the plain browser client is
// enough — no `@supabase/ssr` cookie plumbing to keep in sync.
export function supabaseBrowserClient(): SupabaseClient {
  if (browserClient) return browserClient;
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !anonKey) {
    throw new Error(
      "NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY are not configured",
    );
  }
  browserClient = createClient(url, anonKey);
  return browserClient;
}

export async function currentAccessToken(): Promise<string | null> {
  const {
    data: { session },
  } = await supabaseBrowserClient().auth.getSession();
  return session?.access_token ?? null;
}
