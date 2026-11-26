import Lean.Data.Json
open Lean

namespace Fluxpcb

/-! The canonical board state the kernel reasons about.

    Coordinates are integer nanometres (KiCad's own unit), shapes are the
    three primitives the extractor emits (segment/circle/polygon), and every
    claim/certificate refers to items by id.  `canonical` + `fnv1a` are the
    drift check: the Python extractor builds the same string; any field the
    parser drops changes the digest. -/

inductive Shape where
  | seg (x1 y1 x2 y2 w : Int)
  | circle (cx cy r : Int)
  | poly (pts : Array (Int × Int))
deriving Repr, Inhabited, BEq

structure Item where
  id : String
  net : String
  layers : List String
  shape : Shape
  /-- Drill diameter in nm (-1 = no hole).  Vias and PTH pads carry one. -/
  drill : Int := -1
  /-- Via span as "Top-Bottom" layer names ("" for pads/tracks). -/
  span : String := ""
deriving Repr, Inhabited, BEq

structure State where
  schema : String
  board : String
  layers : List String
  items : Array Item
  rules : List (String × Int)
  netPads : List (String × Array String)
  /-- ref -> pad ids.  Not needed for connectivity, but footprint identity is
      what module contracts split on, so it must be inside the digest and the
      frame: renaming a ref changes the boundary without moving any copper. -/
  footprints : List (String × Array String)
  /-- Filled zone count: zone copper is not extracted, so a board with zones
      cannot claim clearance/edge coverage. -/
  zones : Int := 0
  /-- Board outline polygon (Edge.Cuts), nm. -/
  outline : Array (Int × Int) := #[]
  digest : String
deriving Inhabited

def jInt (j : Json) (k : String) : Except String Int := do
  let v ← j.getObjVal? k
  Json.getInt? v

def jStr (j : Json) (k : String) : Except String String := do
  let v ← j.getObjVal? k
  Json.getStr? v

def jArr (j : Json) (k : String) : Except String (Array Json) := do
  let v ← j.getObjVal? k
  Json.getArr? v

def strList (j : Json) : Except String (List String) := do
  let a ← Json.getArr? j
  a.toList.mapM Json.getStr?

def shapeOf (j : Json) : Except String Shape := do
  let k ← jStr j "kind"
  match k with
  | "seg" => do
      let x1 ← jInt j "x1"; let y1 ← jInt j "y1"
      let x2 ← jInt j "x2"; let y2 ← jInt j "y2"
      let w ← jInt j "w"
      pure (.seg x1 y1 x2 y2 w)
  | "circle" => do
      let cx ← jInt j "cx"; let cy ← jInt j "cy"; let r ← jInt j "r"
      pure (.circle cx cy r)
  | "poly" => do
      let ps ← jArr j "pts"
      let pts ← ps.mapM (fun p => do
        let a ← Json.getArr? p
        if a.size != 2 then throw "poly point needs 2 numbers"
        let x ← Json.getInt? (a[0]!)
        let y ← Json.getInt? (a[1]!)
        pure (x, y))
      pure (.poly pts)
  | other => throw s!"unsupported shape: {other}"

def itemOf (j : Json) : Except String Item := do
  let id ← jStr j "id"
  let net ← jStr j "net"
  let layers ← (j.getObjVal? "layers") >>= strList
  let sh ← (j.getObjVal? "shape") >>= shapeOf
  let drill ← match j.getObjVal? "drill" with
    | .ok v => pure ((Json.getInt? v).toOption.getD (-1))
    | .error _ => pure (-1)
  let span ← match j.getObjVal? "span" with
    | .ok v => pure ((Json.getStr? v).toOption.getD "")
    | .error _ => pure ""
  pure { id := id, net := net, layers := layers, shape := sh,
         drill := drill, span := span }

def jsonInt (v : Json) : Int :=
  (Json.getInt? v).toOption.getD 0

def rulesOf (j : Json) : Except String (List (String × Int)) := do
  let o ← j.getObj?
  pure (o.toList.map (fun (k, v) => (k, jsonInt v)))

def netPadsOf (j : Json) : Except String (List (String × Array String)) := do
  let o ← j.getObj?
  o.toList.mapM (fun (k, v) => do
    let a ← Json.getArr? v
    let ids ← a.mapM Json.getStr?
    pure (k, ids))

def stateOf (j : Json) : Except String State := do
  let schema ← jStr j "schema"
  let board ← jStr j "board"
  let layers ← (j.getObjVal? "layers") >>= strList
  let itemsJ ← jArr j "items"
  let items ← itemsJ.mapM itemOf
  let rulesJ ← j.getObjVal? "rules"
  let rules ← rulesOf rulesJ
  let netsJ ← j.getObjVal? "netPads"
  let netPads ← netPadsOf netsJ
  let fps ← match j.getObjVal? "footprints" with
    | .ok v => netPadsOf v
    | .error _ => pure []
  let zones ← match j.getObjVal? "zones" with
    | .ok v => pure ((Json.getInt? v).toOption.getD 0)
    | .error _ => pure 0
  let outline ← match j.getObjVal? "outline" with
    | .ok v => do
        let a ← Json.getArr? v
        a.mapM (fun p => do
          let q ← Json.getArr? p
          if q.size != 2 then throw "outline point needs 2 numbers"
          let x ← Json.getInt? (q[0]!)
          let y ← Json.getInt? (q[1]!)
          pure (x, y))
    | .error _ => pure #[]
  let digest ← jStr j "digest"
  pure { schema, board, layers, items, rules, netPads, footprints := fps,
         zones := zones, outline := outline, digest }

def shapeStr : Shape → String
  | .seg x1 y1 x2 y2 w => s!"seg:{x1},{y1},{x2},{y2},{w}"
  | .circle cx cy r => s!"cir:{cx},{cy},{r}"
  | .poly pts => "poly:" ++ String.intercalate ";" (pts.toList.map (fun (x, y) => s!"{x},{y}"))

def canonical (s : State) : String :=
  let items := s.items.toList.mergeSort (fun a b => (compare a.id b.id).isLT)
  let rules := s.rules.mergeSort (fun a b => (compare a.1 b.1).isLT)
  let nets := s.netPads.mergeSort (fun a b => (compare a.1 b.1).isLT)
  let fps := s.footprints.mergeSort (fun a b => (compare a.1 b.1).isLT)
  String.intercalate "\n" <|
    ["v2", s.schema, s.board, String.intercalate "," s.layers] ++
    items.map (fun i =>
      s!"i|{i.id}|{i.net}|{String.intercalate "," i.layers}|{shapeStr i.shape}" ++
      (if i.drill >= 0 then s!"|d={i.drill}" else "") ++
      (if i.span != "" then s!"|s={i.span}" else "")) ++
    rules.map (fun (k, v) => s!"r|{k}={v}") ++
    nets.map (fun (n, ids) => s!"n|{n}:{String.intercalate "," ids.toList}") ++
    fps.map (fun (n, ids) => s!"f|{n}:{String.intercalate "," ids.toList}") ++
    [s!"z|{s.zones}"] ++
    (if s.outline.isEmpty then []
     else ["o|" ++ String.intercalate ";" (s.outline.toList.map (fun (x, y) => s!"{x},{y}"))])

def fnv1a (s : String) : UInt64 :=
  let b := s.toUTF8
  let rec go (i : Nat) (h : UInt64) : UInt64 :=
    if i >= b.size then h
    else go (i + 1) ((h ^^^ (b.data[i]!).toUInt64) * 1099511628211)
  go 0 14695981039346656037

def digestOf (s : State) : String := toString (fnv1a (canonical s))

end Fluxpcb
