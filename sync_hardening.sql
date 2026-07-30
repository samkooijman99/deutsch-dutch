-- Optional hardening for the code-keyed sync backend.
-- Run against the project DB (psql or Supabase SQL editor). Safe to re-run.
-- Guards the anon-callable write path against junk codes / oversized payloads.
-- (With the app's compact "incremental" sync, real payloads are only a few KB;
--  the size cap here just bounds abuse from someone hitting the RPC directly.)

create or replace function public.save_deck(p_code text, p_state jsonb)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if char_length(p_code) not between 3 and 64 then
    raise exception 'bad code';
  end if;
  if length(p_state::text) > 5000000 then   -- ~5 MB ceiling
    raise exception 'state too large';
  end if;
  insert into public.decks(code, state, updated_at)
  values (p_code, p_state, now())
  on conflict (code) do update
    set state = excluded.state, updated_at = now();
end;
$$;

grant execute on function public.save_deck(text, jsonb) to anon, authenticated;
