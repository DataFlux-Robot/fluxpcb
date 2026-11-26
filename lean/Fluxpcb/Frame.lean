import Fluxpcb.Board

namespace Fluxpcb

/-- Everything outside `touched` must be identical between the two states. -/
def sameOutside (before after : State) (touched : Array String) : Bool :=
  before.items.all (fun i =>
    touched.contains i.id ||
    match after.items.find? (fun j => j.id == i.id) with
    | some j => i == j
    | none => false) &&
  after.items.all (fun j =>
    touched.contains j.id ||
    (before.items.find? (fun i => i.id == j.id)).isSome)

/-- A reused proof is only valid if its scope was not touched. -/
def scopeDisjoint (scope touched : Array String) : Bool :=
  scope.all (fun x => !touched.contains x)

/-- Rules, layers and the ref→pad map are not items: a change in any of them
    changes what every claim means, so no proof can be carried across it. -/
def contextSame (before after : State) : Bool :=
  before.rules == after.rules &&
  before.layers == after.layers &&
  before.footprints == after.footprints

def frameOk (before after : State) (touched : Array String)
    (carried : Array (String × Array String)) : Bool :=
  sameOutside before after touched &&
  contextSame before after &&
  carried.all (fun (_, scope) => scopeDisjoint scope touched)

end Fluxpcb
