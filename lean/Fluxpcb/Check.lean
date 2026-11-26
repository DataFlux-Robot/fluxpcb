import Fluxpcb.Claim
import Fluxpcb.Geom
import Std.Data.HashSet

namespace Fluxpcb

def itemById (s : State) (id : String) : Option Item :=
  s.items.find? (fun i => i.id == id)

partial def bfs (edges : Array (String × String)) (start : String) : Std.HashSet String :=
  let rec go (frontier : List String) (seen : Std.HashSet String) : Std.HashSet String :=
    match frontier with
    | [] => seen
    | x :: xs =>
      if seen.contains x then go xs seen
      else
        let seen := seen.insert x
        let nb := edges.foldl (fun acc (a, b) =>
          if a == x then b :: acc else if b == x then a :: acc else acc) []
        go (nb ++ xs) seen
  go [start] (∅ : Std.HashSet String)

/-- `none` = every edge checks; `some why` = a malformed edge. -/
def checkEdges (s : State) (c : Claim) (cert : Cert) : Option String :=
  cert.edges.findSome? (fun (a, b) =>
    match itemById s a, itemById s b with
    | some ia, some ib =>
        if ia.net == c.net && ib.net == c.net && itemTouch ia ib then none
        else some s!"edge {a}-{b}: not touching copper of net {c.net}"
    | _, _ => some s!"edge {a}-{b}: unknown item")

/-- Pads the certificate's tree does not reach. -/
def missingPads (s : State) (c : Claim) (cert : Cert) : Array String :=
  if c.pads.isEmpty then #[]
  else
    let seen := bfs cert.edges (c.pads[0]!)
    c.pads.filter (fun p => !seen.contains p)

def normNets (l : List (String × Array String)) : List (String × List String) :=
  l.map (fun (n, ids) => (n, ids.toList.mergeSort (fun a b => (compare a b).isLT)))
   |>.mergeSort (fun a b => (compare a.1 b.1).isLT)

/-- `none` = the board's nets equal the expected netlist. -/
def checkNetlist (s : State) (c : Claim) : Option String :=
  let want := normNets c.expect
  let got := normNets s.netPads
  if want == got then none
  else
    let bad := want.findSome? (fun (n, ids) =>
      match got.find? (fun (m, _) => m == n) with
      | some (_, g) => if g == ids then none else some n
      | none => some n)
    match bad with
    | some n => some s!"net {n} differs from the expected netlist"
    | none => some "extra nets on the board"

/-- Status of one claim: proven / refuted / invalid / unknown / foreign. -/
def statusOf (s : State) (c : Claim) (cert? : Option Cert) : String × String :=
  match c.pred with
  | "connected" =>
      match cert? with
      | none => ("unknown", "no certificate (search produced no witness)")
      | some cert =>
          match checkEdges s c cert with
          | some w => ("invalid", w)
          | none =>
              let missing := missingPads s c cert
              if missing.isEmpty then ("proven", "")
              else ("unknown", s!"pads not reached: {String.intercalate "," missing.toList}")
  | "netlist" =>
      match checkNetlist s c with
      | none => ("proven", "")
      | some w => ("refuted", w)
  | other => ("foreign", s!"checked by the {other} domain kernel")

end Fluxpcb
