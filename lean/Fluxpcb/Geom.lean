import Fluxpcb.Board

namespace Fluxpcb

/-! Exact integer geometry (nanometres).  Only what connectivity needs:
    do two copper items touch?  Clearance lives in the fab domain kernel, not
    here — the Lean kernel owns the logical/geometric *adjacency*. -/

abbrev Pt := Int × Int

def cross (o a b : Pt) : Int :=
  (a.1 - o.1) * (b.2 - o.2) - (a.2 - o.2) * (b.1 - o.1)

def between (a b p : Pt) : Bool :=
  min a.1 b.1 ≤ p.1 && p.1 ≤ max a.1 b.1 &&
  min a.2 b.2 ≤ p.2 && p.2 ≤ max a.2 b.2

def onSeg (a b p : Pt) : Bool := cross a b p == 0 && between a b p

def segsIntersect (a b c d : Pt) : Bool :=
  if onSeg a b c || onSeg a b d || onSeg c d a || onSeg c d b then true
  else
    let o1 := cross a b c
    let o2 := cross a b d
    let o3 := cross c d a
    let o4 := cross c d b
    (o1 > 0) != (o2 > 0) && (o3 > 0) != (o4 > 0)

/-- Squared distance from p to segment a-b, as (numerator, denominator). -/
def ptSegD2 (p a b : Pt) : Int × Int :=
  let abx := b.1 - a.1
  let aby := b.2 - a.2
  let den := abx * abx + aby * aby
  if den == 0 then ((p.1 - a.1) * (p.1 - a.1) + (p.2 - a.2) * (p.2 - a.2), 1)
  else
    let apx := p.1 - a.1
    let apy := p.2 - a.2
    let dot := apx * abx + apy * aby
    if dot ≤ 0 then (apx * apx + apy * apy, 1)
    else if dot ≥ den then
      ((p.1 - b.1) * (p.1 - b.1) + (p.2 - b.2) * (p.2 - b.2), 1)
    else
      let cr := abx * apy - aby * apx
      (cr * cr, den)

def ptSegLe (p a b : Pt) (r2 : Int) : Bool :=
  let (num, den) := ptSegD2 p a b
  num ≤ r2 * den

/-- Distance-to-copper test: the point's distance to the segment is at most
    half of `s`, where `s` is the sum of the two full widths/diameters.
    (ptSegLe takes a squared radius; widths are full widths, hence 4·num.) -/
def ptSegTouch (p a b : Pt) (s : Int) : Bool :=
  let (num, den) := ptSegD2 p a b
  4 * num ≤ s * s * den

/-- Even-odd point in polygon. -/
def ptInPoly (p : Pt) (pts : Array Pt) : Bool :=
  let n := pts.size
  if n < 3 then false
  else
    let rec go (i : Nat) (acc : Bool) : Bool :=
      if i ≥ n then acc
      else
        let a := pts[i]!
        let b := pts[(i + 1) % n]!
        let cond := (a.2 > p.2) != (b.2 > p.2)
        let inside :=
          if cond then
            let lhs := (p.1 - a.1) * (b.2 - a.2)
            let rhs := (b.1 - a.1) * (p.2 - a.2)
            if b.2 > a.2 then lhs < rhs else lhs > rhs
          else false
        go (i + 1) (if inside then !acc else acc)
    go 0 false

def polyEdges (pts : Array Pt) : Array (Pt × Pt) :=
  let n := pts.size
  if n < 2 then #[] else
  (Array.range n).map (fun i => (pts[i]!, pts[(i + 1) % n]!))

def anyEdge (pts : Array Pt) (f : Pt → Pt → Bool) : Bool :=
  (polyEdges pts).any (fun (a, b) => f a b)

def circleCircle (c1 c2 : Int × Int × Int) : Bool :=
  let (x1, y1, r1) := c1
  let (x2, y2, r2) := c2
  let dx := x1 - x2
  let dy := y1 - y2
  dx * dx + dy * dy ≤ (r1 + r2) * (r1 + r2)

def circleSeg (c : Int × Int × Int) (a b : Pt) (w : Int) : Bool :=
  let (cx, cy, r) := c
  ptSegTouch (cx, cy) a b (2 * r + w)

def circlePoly (c : Int × Int × Int) (pts : Array Pt) : Bool :=
  let (cx, cy, r) := c
  if ptInPoly (cx, cy) pts then true
  else anyEdge pts (fun a b => ptSegTouch (cx, cy) a b (2 * r))

def segSeg (a b : Pt) (w1 : Int) (c d : Pt) (w2 : Int) : Bool :=
  if segsIntersect a b c d then true
  else
    let s := w1 + w2
    ptSegTouch a c d s || ptSegTouch b c d s ||
    ptSegTouch c a b s || ptSegTouch d a b s

def segPoly (a b : Pt) (w : Int) (pts : Array Pt) : Bool :=
  if ptInPoly a pts || ptInPoly b pts then true
  else if anyEdge pts (fun c d => segsIntersect a b c d) then true
  else anyEdge pts (fun c _ => ptSegTouch c a b w)

def polyPoly (p q : Array Pt) : Bool :=
  if anyEdge p (fun a b => anyEdge q (fun c d => segsIntersect a b c d)) then true
  else if p.any (fun v => ptInPoly v q) then true
  else q.any (fun v => ptInPoly v p)

def shapeTouch : Shape → Shape → Bool
  | .seg x1 y1 x2 y2 w, .seg a1 b1 a2 b2 w2 =>
      segSeg (x1, y1) (x2, y2) w (a1, b1) (a2, b2) w2
  | .seg x1 y1 x2 y2 w, .circle cx cy r =>
      circleSeg (cx, cy, r) (x1, y1) (x2, y2) w
  | .circle cx cy r, .seg x1 y1 x2 y2 w =>
      circleSeg (cx, cy, r) (x1, y1) (x2, y2) w
  | .circle cx cy r, .circle dx dy r2 => circleCircle (cx, cy, r) (dx, dy, r2)
  | .circle cx cy r, .poly pts => circlePoly (cx, cy, r) pts
  | .poly pts, .circle cx cy r => circlePoly (cx, cy, r) pts
  | .seg x1 y1 x2 y2 w, .poly pts => segPoly (x1, y1) (x2, y2) w pts
  | .poly pts, .seg x1 y1 x2 y2 w => segPoly (x1, y1) (x2, y2) w pts
  | .poly p, .poly q => polyPoly p q

def sharedLayer (a b : Item) : Bool :=
  a.layers.any (fun l => b.layers.contains l)

def itemTouch (a b : Item) : Bool :=
  a.net == b.net && sharedLayer a b && shapeTouch a.shape b.shape

end Fluxpcb
