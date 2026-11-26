import Fluxpcb
open Fluxpcb

def main (args : List String) : IO UInt32 := do
  match args with
  | [path] =>
      let txt ← IO.FS.readFile path
      match Fluxpcb.run txt with
      | Except.ok out => IO.println out; pure 0
      | Except.error e => IO.eprintln s!"kernel error: {e}"; pure 2
  | _ => IO.eprintln "usage: flux-kernel <request.json>"; pure 1
