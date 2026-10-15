"""Memory layout of FTL: Advanced Edition 1.6.12, Linux amd64 (GOG build).

Every offset below is only valid for the binary whose SHA-256 is BINARY_SHA256.

Provenance labels:
  DWARF   read from the DWARF debug info shipped inside FTL.amd64
          (gdb `ptype /o` / gdb.Type.fields()). Exact, not guessed.
  REA     confirmed by decompiling the function named next to it with REA.
  RUNTIME semantics still to be confirmed against a live game.

FTL.amd64 is a non-PIE executable, so static addresses never move (no ASLR
for the main image). Heap objects are reached by following pointers from
P_THE_APP.

std::string in this binary is the old GCC copy-on-write string: the object is
a single pointer to the character data, and the length is stored as a size_t
24 bytes before that pointer.
"""

BINARY_SHA256 = "0f6407705d7b381237460855e8099878c70de193f6da9845e493cf052ce2db27"

# --- static globals --------------------------------------------------------

P_THE_APP = 0xA30120        # static CApp *p_theApp (CApp.cpp)               DWARF
MOUSE_CONTROL = 0xA37760    # MouseControl MouseControl::Mouse                 DWARF

# CFPS::FPSControl (DWARF): FTL's frame timer. Each frame's time step is
# SpeedFactor = elapsed_seconds * (16 + 7 * speedLevel), capped at 0.5 (CFPS::OnLoop),
# so speedLevel 0 is normal speed and the cap limits the speed-up to about fps / 32.
# Nothing in the game changes speedLevel (Increase/Decrease/NormalSpeedLevel have no callers).
SHIP_INFO_LIST = 0xA34BC0      # static vector<ShipInfo> ShipObject::shipInfoList, indexed by ship id  DWARF
SHIP_INFO_SIZE = 0x68          # ShipInfo: augList map<string,int> +0x0, equipList +0x30, augCount +0x60
CFPS = 0xA3A9C0
CFPS_FPS = CFPS + 0x1C              # frames counted in the last second
CFPS_SPEED_FACTOR = CFPS + 0x18     # float, this frame's time step
CFPS_SPEED_LEVEL = CFPS + 0x24      # int
SPEED_LEVEL_RANGE = (0, 112)        # what ftl_host will write: 1x .. 50x
# Turbo: draw only every Nth frame. CApp::OnExecute calls CApp::OnRender (which draws the
# whole frame and swaps buffers) after every CApp::OnLoop; drawing is what limits FTL's frame
# rate in a software-rendered window, and game speed is capped per frame (CFPS::OnLoop), so
# skipping draws runs more game frames per second. ftl_host redirects that one call to a
# 26-byte hook in the unused tail of the code segment; the hook counts frames in the unused
# tail of .bss. Only in the running game's memory; FTL.amd64 on disk is never touched.  REA
RENDER_CALL = 0x41CD6D              # call CApp::OnRender, in CApp::OnExecute
RENDER_CALL_ORIGINAL = bytes.fromhex("e83ef1ffff")
RENDER_FUNC = 0x41BEB0              # CApp::OnRender
RENDER_HOOK = 0x827D00              # unused, zero-filled tail of the r-x segment (ends 0x827C58)
RENDER_COUNTER = 0xA7DF00           # unused tail of .bss (ends 0xA7D060)
RENDER_EVERY = 0xA7DF04             # int: draw 1 frame in N
RENDER_SKIP_RANGE = (1, 16)
SETTINGS_ALT_PAUSE = 0xA3C179       # Settings::settings.altPause: pause when FTL loses focus
SETTINGS_FULLSCREEN = 0xA3C160      # Settings::settings.fullscreen: fullscreen also pauses on focus loss
MOUSE_POSITION = 0x00       # Point: last mouse position, in game coordinates
MOUSE_AIMING_REQUIRED = 0x10  # int: non-zero while a weapon is armed for targeting
POWER_MANAGERS = 0xA40910   # std::vector<PowerManager> PowerManager::powerManagers  DWARF
POWER_MANAGER_SIZE = 0x2C   # GetPowerManager(id) = begin + id * 0x2c         REA

# System type ids, decoded from the ShipSystem::SystemIdToName jump table.  REA
SYSTEM_NAMES = {
    0: "shields", 1: "engines", 2: "oxygen", 3: "weapons", 4: "drones",
    5: "medbay", 6: "pilot", 7: "sensors", 8: "doors", 9: "teleporter",
    10: "cloaking", 11: "artillery", 12: "battery", 13: "clonebay",
    14: "mind", 15: "hacking", 17: "reactor",
}
SYS_SHIELDS, SYS_ENGINES, SYS_OXYGEN, SYS_WEAPONS, SYS_DRONES = 0, 1, 2, 3, 4
SYS_CLOAKING, SYS_ARTILLERY, SYS_BATTERY = 10, 11, 12


class CApp:                    # size 0x3888                                 DWARF
    SIZE = 0x3888
    GUI = 0x10                 # CommandGui*
    WORLD = 0x18               # WorldManager*
    MENU = 0x20                # MainMenu (embedded); MainMenu.bOpen at +0x0
    # Mouse scaling. CApp::OnMouseMove computes                                REA
    #   game = int((window - bar) * mouseModifier) - modifier
    MODIFIER = 0x3830          # Point modifier_x/y
    FOCUS = 0x383B             # bool focus
    MOUSE_MODIFIER = 0x3844    # float mouseModifier_x/y
    BAR = 0x385C               # int x_bar/y_bar (letterbox)


class WorldManager:            # size 0x15A8                                 DWARF
    PLAYER_SHIP = 0x00         # CompleteShip*
    SPACE = 0x10               # SpaceManager (embedded)
    STAR_MAP = 0x4E8           # StarMap (embedded)
    LAST_LOCATION_EVENT = 0x11C8  # LocationEvent*: the event whose choices are on screen  REA
    STARTED_GAME = 0x1528      # bool bStartedGame
    LOADING_GAME = 0x1529      # bool bLoadingGame


class SpaceManager:            # size 0x4B8                                  DWARF
    ASTEROIDS_RUNNING = 0x18 + 0xB0  # AsteroidGenerator.bRunning
    DANGER_ZONE = 0x100        # bool
    SUN = 0x350                # bool sunLevel: solar flares
    PULSAR = 0x351             # bool pulsarLevel: ion pulses
    PDS = 0x370                # bool bPDS: enemy anti-ship battery
    NEBULA = 0x439             # bool bNebula
    STORM = 0x43A              # bool bStorm
    GAME_PAUSED = 0x47C        # bool gamePaused


class CompleteShip:            # size 0x218                                  DWARF
    SIZE = 0x218
    SHIP_ID = 0x08             # int iShipId
    SHIP_MANAGER = 0x10        # ShipManager*
    ENEMY_SHIP = 0x20          # CompleteShip*
    IS_PLAYER = 0x28           # bool bPlayerShip
    AI = 0x30                  # ShipAI (embedded)
    AI_SURRENDERED = 0x30 + 0x139
    AI_ESCAPING = 0x30 + 0x13A


class BossShip:                # derives CompleteShip (the Flagship)          DWARF
    CURRENT_STAGE = 0x214      # int: 1..3
    POWER_TIMER = 0x218        # TimerHelper: the next power surge (stage 2 drones, stage 3 barrage / Zoltan shield)
    POWER_COUNT = 0x22C        # int: surges so far (stage 3: every 4th, count % 4 == 3, is the Zoltan shield)
    DEATH_BEGAN = 0x248        # bool: the final stage is blowing up (victory)


class TimerHelper:             # size 0x14                                   DWARF
    CURR_TIME = 0x08           # float
    CURR_GOAL = 0x0C           # float
    RUNNING = 0x11             # bool


class ArtillerySystem:         # derives ShipSystem (a Flagship weapon pod, or a cruiser's artillery)  DWARF
    PROJECTILE_FACTORY = 0x248 # ProjectileFactory*: the pod's weapon
    CLOAKED = 0x258            # bool


class ToggleSystem:            # CloakingSystem / BatterySystem, derive ShipSystem  DWARF
    TURNED_ON = 0x244          # bool bTurnedOn
    TIMER = 0x248              # TimerHelper: how long it stays on


class ShipManager:             # size 0xA58                                  DWARF
    SIZE = 0xA58
    # Targetable base (+0x10) -> bool hostile. True for an AUTO_ASSAULT mid-fight while
    # hostile_ship (+0x614) read False, so this is the flag to trust.          verified live
    TARGETABLE_HOSTILE = 0x10 + 0x0C
    SYSTEM_LIST = 0x28         # vector<ShipSystem*>
    OXYGEN_SYSTEM = 0x40       # OxygenSystem*
    SHIELD_SYSTEM = 0x80       # Shields*
    WEAPON_SYSTEM = 0x88       # WeaponSystem*
    DRONE_SYSTEM = 0x90        # DroneSystem*
    CREW_LIST = 0xC0           # vector<CrewMember*>
    SHIP = 0x118               # Ship (embedded)
    GAME_OVER = 0x5F8          # bool bGameOver
    CURRENT_TARGET = 0x600     # ShipManager*
    JUMP_TIMER = 0x608         # pair<float,float>  (charge, goal)
    FUEL = 0x610               # int fuel_count
    HOSTILE = 0x614            # bool hostile_ship: stays False in fights; use TARGETABLE_HOSTILE
    DESTROYED = 0x615          # bool bDestroyed
    LAST_DAMAGE = 0x618        # int iLastDamage
    JUMP_ANIMATING = 0x620 + 0x14  # jumpAnimation (AnimationTracker).running: the arrival animation
                                   # is playing; dialogs ignore input meanwhile          RUNTIME
    SYSTEM_KEY = 0x658         # vector<int>: system type -> index in SYSTEM_LIST or -1
    SCRAP = 0x670              # int currentScrap           (also REA: ModifyScrapCount)
    JUMPING = 0x674            # bool bJumping
    AUTOMATED = 0x675          # bool bAutomated
    BLUEPRINT = 0x680          # ShipBlueprint (embedded)
    TEMP_DRONE_COUNT = 0x8F0   # used when there is no drone system  (REA: GetDroneCount)
    TEMP_MISSILE_COUNT = 0x8F4  # used when there is no weapon system (REA: GetMissileCount)


class Door:                   # size 0x260                                  DWARF
    SIZE = 0x260
    ROOM1 = 0x1C               # int
    ROOM2 = 0x20               # int; -1 for an airlock (the other side is space)
    OPEN = 0x24                # bool bOpen
    DOOR_ID = 0x1C8            # int
    X = 0x250                  # int: door centre, in the same ship space as room rects  verified live
    Y = 0x254                  # int
    VERTICAL = 0x258           # bool


class Ship:                    # size 0x490, embedded at ShipManager+0x118   DWARF
    ROOM_LIST = 0x10           # vector<Room*>
    DOOR_LIST = 0x28           # vector<Door*>: interior doors
    AIRLOCKS = 0x58            # vector<Door*>: doors to space
    OUTER_WALLS = 0x40         # vector<OuterHull*>; a wall with fDamage > 0 is a breach,
                               # in the room under pLoc + 17px  (REA: Ship::ContainsHullBreach)
    HULL = 0x70                # pair<int,int> hullIntegrity (current, max)
    SHIP_NAME = 0x178          # std::string
    CLOAKED = 0x470            # bool


class Blueprint:               # size 0x78                                   DWARF
    NAME = 0x08                # std::string (blueprint id, e.g. "LASER_BURST_3")


class ShipBlueprint:           # size 0x250, embedded at ShipManager+0x680   DWARF
    BLUEPRINT_NAME = 0xD8      # std::string


class ShipSystem:              # size 0x248                                  DWARF
    SIZE = 0x248
    DAMAGE = 0x1C              # float fDamage   (Repairable)
    NAME = 0x30                # std::string     (Repairable)
    ROOM_ID = 0x38             # int roomId      (Repairable)
    TYPE = 0x40                # int iSystemType
    NEEDS_MANNED = 0x44        # bool
    MANNED = 0x45              # bool bManned
    ACTIVE_MANNED = 0x48       # int iActiveManned
    POWER = 0x50               # pair<int,int> powerState: (powered bars, system level)  verified live
    REQUIRED_POWER = 0x58      # int
    ON_FIRE = 0x170            # bool bOnFire
    BREACHED = 0x171           # bool bBreached
    HEALTH = 0x174             # pair<int,int> healthState: (undamaged bars, system level)  verified live
    LOCK_COUNT = 0x19C         # int iLockCount (ion lock)
    BONUS_POWER = 0x1E4        # int iBonusPower
    MAX_LEVEL = 0x218          # int maxLevel: upgrade cap (8 for shields, 3 for subsystems)
    BATTERY_POWER = 0x21C      # int iBatteryPower
    HACK_EFFECT = 0x220        # int iHackEffect


class Shields:                 # size 0x368, derives ShipSystem             DWARF
    SIZE = 0x368
    CHARGER = 0x26C            # float shields.charger: seconds into recharging the next layer
    LAYERS = 0x270             # ShieldPower.first/second: (layers up, max layers)  verified live
    SUPER = 0x278              # pair<int,int> super shield


class WeaponSystem:            # size 0x2F0, derives ShipSystem             DWARF
    SIZE = 0x2F0
    WEAPONS = 0x250            # vector<ProjectileFactory*>
    MISSILE_COUNT = 0x288      # int                       (REA: GetMissileCount)
    SLOT_COUNT = 0x2C0         # int


class DroneSystem:             # size 0x2C8, derives ShipSystem             DWARF
    SIZE = 0x2C8
    DRONES = 0x248             # vector<Drone*>
    DRONE_COUNT = 0x260        # int                       (REA: GetDroneCount)


class Drone:                   # size 0x40                                   DWARF
    SIZE = 0x40
    POWERED = 0x10             # bool
    DEPLOYED = 0x18            # bool
    BLUEPRINT = 0x20           # const DroneBlueprint*: name +0x8, typeName +0x78 ("DEFENSE", "COMBAT", ...)
    DEAD = 0x28                # bool


DRONE_BLUEPRINT_TYPE_NAME = 0x78


class OxygenSystem:            # size 0x268, derives ShipSystem             DWARF
    SIZE = 0x268
    MAX_OXYGEN = 0x244         # float
    LEVELS = 0x248             # vector<float>, one per room id
    TOTAL = 0x260              # float fTotalOxygen


class ProjectileFactory:       # size 0x630 (a weapon)                       DWARF
    SIZE = 0x630
    COOLDOWN = 0x0C            # pair<float,float> (charge, goal)
    BLUEPRINT = 0x20           # const WeaponBlueprint*
    AUTO_FIRING = 0xF0         # bool
    FIRE_WHEN_READY = 0xF1     # bool
    POWERED = 0xF2             # bool
    REQUIRED_POWER = 0xF4      # int
    TARGETS = 0xF8             # vector<Pointf>
    NAME = 0x130               # std::string
    CHARGE_LEVEL = 0x620       # int (chain/charge weapons)
    IS_ARTILLERY = 0x62C       # bool


class DroneBlueprint:          # DWARF
    TYPE_NAME = 0x78           # std::string ("COMBAT", "DEFENSE", "SHIP_REPAIR", ...)
    POWER = 0x88               # int


class ShipSlots:               # DWARF
    WEAPON_SYSTEM = 0x88       # ShipManager: WeaponSystem*
    DRONE_SYSTEM = 0x90        # ShipManager: DroneSystem*
    WEAPON_SLOTS = 0x2C0       # WeaponSystem.slot_count
    DRONE_SLOTS = 0x298        # DroneSystem.slot_count
    DRONES = 0x248             # DroneSystem.drones (vector<Drone*>)


class WeaponBlueprint:         # size 0x1A8                                  DWARF
    SIZE = 0x1A8
    TYPE_NAME = 0x78           # std::string ("LASER", "MISSILES", "BEAM", ...)
    DAMAGE = 0x80              # Damage (embedded): iDamage +0x0, iShieldPiercing +0x4, iIonDamage +0x14
    SHOTS = 0xB4               # int
    MISSILES = 0xB8            # int missiles used per volley
    COOLDOWN = 0xBC            # float
    POWER = 0xC0               # int


class CrewMember:              # size 0x748                                  DWARF
    SIZE = 0x748
    X = 0x0C                   # float
    Y = 0x10                   # float
    GOAL_X = 0x1C              # float: where the crew member is walking to
    GOAL_Y = 0x20              # float
    HEALTH = 0x2C              # pair<float,float> (current, max)
    NEW_PATH = 0x70            # bool: an order is queued; the path is computed once time runs  verified live
    CURRENT_REPAIR = 0x88      # Repairable*; non-null while repairing     (REA: CrewMember::Repairing)
    SLOT_ROOM = 0xAC           # int currentSlot.roomId: room the crew member is assigned to; set the
                               # moment an order is given, even while paused         verified live
    ROOM_ID = 0x9C             # int iRoomId
    MANNING_ID = 0xA0          # int iManningId: system type being manned, -1 if none
    # iRepairId (+0xA4) is never initialised in this build: it reads as 0x20202020.
    INTRUDER = 0xBC            # bool
    FIGHTING = 0xBD            # bool bFighting
    SHIP_ID = 0x18C            # int currentShipId
    FROZEN = 0x1D4             # bool bFrozen
    SPECIES = 0x218            # std::string
    DEAD = 0x220               # bool bDead
    ON_FIRE = 0x224            # int iOnFire: > 0 while extinguishing a fire (REA: CrewMember::RepairingFire)
    CREW_NAME = 0x240 + 0x78   # CrewBlueprint.crewName (TextString.data, std::string)
    MIND_CONTROLLED = 0x531    # bool


class Room:                    # size 0x388                                  DWARF
    READ_SIZE = 0xC0           # only the leading fields are needed
    RECT = 0x1C                # Globals::Rect {x, y, w, h} in ship pixels (ints)
    ROOM_ID = 0x2C             # int
    # iFireCount (+0x90) is not the live fire count: it stayed 0 in a burning room.
    LAST_O2 = 0xB8             # float


TILE_SIZE = 35                 # ShipGraph::TranslateToGrid divides by 35       REA


class Repairable:              # base of systems, fires and hull walls        DWARF
    DAMAGE = 0x1C              # float fDamage; NeedsRepairing() is fDamage > 0.0  REA
    LOCATION = 0x20            # Point pLoc


class FireSpreader:            # Spreader<Fire>, embedded at ShipManager+0xD8 DWARF
    OFFSET_IN_SHIP_MANAGER = 0xD8
    GRID = 0x28                # vector<vector<Fire>>, indexed [x tile][y tile]
    FIRE_SIZE = 0x1E0          # a tile burns when its Fire.fDamage > 0  (REA: Spreader<Fire>::CountInRoom)


class PowerManager:            # size 0x2C                                   DWARF
    CURRENT_POWER = 0x00       # pair<int,int>: (power in use, reactor size)  verified live
    BATTERY_POWER = 0x24       # pair<int,int>


class FocusWindow:             # base of most UI windows, size 0x20          DWARF
    OPEN = 0x08                # bool bOpen


class CommandGui:              # size 0x4798                                 DWARF
    SIZE = 0x4798
    CREW_CONTROL = 0x268       # CrewControl (embedded)
    COMBAT_CONTROL = 0x588     # CombatControl (embedded)
    STAR_MAP = 0x1968          # StarMap*
    SHIP_POSITION = 0x1A00     # Point: screen origin of the player ship's room rects
                               #   (REA: CommandGui::GetWorldCoordinates)
    CHOICE_BOX = 0x1A20        # ChoiceBox (embedded, derives FocusWindow)
    GAME_OVER = 0x1C38         # bool gameover
    MENU_BOX = 0x1C40          # MenuScreen (embedded, derives FocusWindow)
    GAME_OVER_SCREEN = 0x20B0  # GameOver (embedded, derives FocusWindow)
    PAUSED = 0x3188            # bool bPaused
    AUTO_PAUSED = 0x3189       # bool bAutoPaused
    MENU_PAUSE = 0x318A        # bool menu_pause
    EVENT_PAUSE = 0x318B       # bool event_pause
    SHIP_SCREENS = 0x3200      # TabbedWindow (upgrades / crew / equipment)
    EQUIP_SCREEN = 0x3C00      # Equipment (the ship screen's third tab, and the store's Sell tab)  DWARF
    STORE_SCREENS = 0x3380     # TabbedWindow
    UPGRADE_BUTTON = 0x3F48    # Button on the HUD; LButtonDown opens SHIP_SCREENS when hovered  REA
    STORE_BUTTON = 0x40B8      # TextButton on the HUD; active at a store beacon. LButtonDown opens
                               #   STORE_SCREENS when it is active and hovered        REA
    ENEMY_SHIP = 0x4298        # CompleteShip*
    JUMP_COMPLETE = 0x42C0     # bool
    CHOICE_BOX_OPEN = 0x4572   # bool


class CrewControl:             # embedded at CommandGui+0x268                DWARF
    SELECTED_CREW = 0x08       # vector<CrewMember*>
    SELECTED_DOOR = 0x38       # Door*: the door under the mouse
    CREW_BOXES = 0x60          # vector<CrewBox*>: the crew panel on the left


class CrewBox:                 # size 0x370                                  DWARF
    READ_SIZE = 0x138
    CREW = 0x20                # CrewMember*
    NUMBER = 0x130             # int, 0-based: hotkey "crew<number + 1>" selects it (F1 -> number 0)  verified live
    SELECTABLE = 0x134         # bool


class ChoiceBox:               # size 0x218                                  DWARF
    MAIN_TEXT = 0x30           # std::string
    CHOICES = 0x38             # vector<ChoiceText>
    CHOICE_RECTS = 0x58        # vector<Globals::Rect>: on-screen box of each choice


class ChoiceText:              # size 0x188                                  DWARF
    SIZE = 0x188
    TYPE = 0x00                # int
    TEXT = 0x08                # std::string
    REWARDS = 0x10             # ResourceEvent: missiles +0x0, fuel +0x4, drones +0x8, scrap +0xC, crew +0x10


class CombatControl:           # embedded at CommandGui+0x588                DWARF
    # WeaponControl (+0x20) -> ProjectileFactory* armedWeapon (+0xD8): the weapon selected
    # for aiming. FTL crashed in CanTargetSelf when this dangled across a LoadGame
    # (crash.10.14.00.06.txt), so the bot never leaves a weapon armed.
    ARMED_WEAPON = 0x20 + 0xD8
    CURRENT_TARGET = 0x7C0     # CompleteShip*
    SELECTED_ROOM = 0x7D8      # int: enemy room under the mouse (CombatControl::SelectRoom)  REA
    # Enemy room rects start at POSITION + TARGET_POSITION on screen.
    #   (REA: CombatControl::SelectRoom / GetEnemyPosition)
    POSITION = 0x7D0           # Point
    TARGET_POSITION = 0x7E0    # Point
    OPEN = 0xEB8               # bool


class StarMap:                 # size 0xCD0, derives FocusWindow             DWARF
    SIZE = 0xCD0
    LOCATIONS = 0x28           # vector<Location*>
    CURRENT_LOC = 0x88         # Location*
    POTENTIAL_LOC = 0x90       # Location*: beacon a click would travel to
    HOVER_LOC = 0x98           # Location*: beacon under the mouse
    # A beacon is drawn (and hovered) at POSITION + TRANSLATION + Location.loc
    #   (REA: StarMap::MouseMove). A click with POTENTIAL_LOC set travels at once
    #   (StarMap::MouseClick sets readyToTravel and closes the map).
    POSITION = 0xA0            # Point
    TRANSLATION = 0xB8         # Pointf
    READY_TO_TRAVEL = 0xC0     # bool
    FLEET_CENTER = 0xC4        # Point dangerZone: rebel fleet circle, in Location.loc space  verified live
    FLEET_RADIUS = 0xCC        # float dangerZoneRadius: beacons inside are overtaken          verified live
    END_BUTTON = 0xD8          # TextButton "next sector" (GenericButton: hitbox +0x10, bActive +0x48)
    WAIT_BUTTON = 0x1D8        # TextButton "Wait": shown when out of fuel; passes time here  DWARF
    DISTRESS_BUTTON = 0x2D8    # DistressButton (a TextButton) "Distress beacon"              DWARF
    DISTRESS_ON = 0x2D8 + 0x120  # bool DistressButton.state                               DWARF
    OUT_OF_FUEL = 0x820        # bool outOfFuel                                            DWARF
    WORLD_LEVEL = 0x500        # int (0-based sector number)
    PURSUIT_DELAY = 0x508      # int: < 0 holds the fleet back. StarMap::GetNextDangerMove: the
                               #   fleet advances 64 per jump (128 once this is >= 1, half from a
                               #   nebula beacon, 0 while it is < 0)                             REA
    NEBULA_MAP = 0xAE1         # bool bNebulaMap
    CURRENT_SECTOR = 0x8A8     # Sector*; its neighbors are the next-sector choices
    CHOOSING_SECTOR = 0x8B8    # bool bChoosingNewSector: the sector map is showing
    SECTOR_MAP_OFFSET = 0xCA8  # Point
    SECTOR_HIT_BOXES = 0xCB8   # vector<Rect>; box N is CURRENT_SECTOR->neighbors[N], at OFFSET + box
    # Sector 8, the Last Stand (StarMap::UpdateBoss, LocationHasBoss)                         DWARF/REA
    BOSS_LOC = 0xAA0           # int: index of the Flagship's beacon in BOSS_PATH
    ARRIVED_AT_BASE = 0xAA4    # int: turns the Flagship has spent at the Federation base (it attacks it)
    BOSS_JUMPING = 0xAA9       # bool: the Flagship moves on to the next beacon of its path on our next jump
    BOSS_PATH = 0xAB0          # vector<Location*>: its route; the last one is the base (Location.beacon)
    BOSS_LEVEL = 0xAC8         # bool: this is the Last Stand
    BOSS_STAGE = 0xC50         # int


class Equipment:               # size 0x338, derives FocusWindow (CommandGui.equipScreen)  DWARF
    SELL_BOX = 0x140           # DropBox (position Point +0x0): drop an item here to sell it (store mode)
    BOXES = 0x1D8              # vector<EquipmentBox*>: weapon / drone / augment slots, cargo, overflow
    OVERCAPACITY_BOX = 0x208   # EquipmentBox*: the item that does not fit (discarded on close)
    OVER_AUG_BOX = 0x210       # AugmentEquipBox*
    DRAGGING = 0x224           # bool bDragging
    OVER_CAPACITY = 0x328      # bool
    OVER_AUG_CAPACITY = 0x329  # bool
    STORE_MODE = 0x32A         # bool: shown as the store's Sell tab


class EquipmentBox:            # size 0xA8                                   DWARF
    SIZE = 0xA8
    LOCATION = 0x58            # Point
    HITBOX = 0x60              # Globals::Rect (game coords, verified live)
    ITEM = 0x70                # EquipmentBoxItem: pWeapon +0x0, pDrone +0x8, pCrew +0x10, augment +0x18
    HOVER = 0x90               # bool bMouseHovering
    BLOCKED = 0x92             # bool
    SLOT = 0x94                # int
    LOCKED = 0x98              # bool


EQUIP_BOX_VTABLES = {          # vtable symbol + 0x10                         nm
    0x776F90: "cargo", 0x7771D0: "weapon", 0x777290: "drone", 0x777350: "augment", 0x777050: "crew",
}


class GenericButton:           # size 0x50                                   DWARF
    HITBOX = 0x10              # Globals::Rect
    ACTIVE = 0x48              # bool
    HOVER = 0x49               # bool: set by MouseMove when the mouse is over it


class TabbedWindow:            # size 0x180, derives FocusWindow             DWARF
    BUTTONS = 0x20             # vector<Button*>: one tab button per window
    WINDOWS = 0x38             # vector<FocusWindow*>; the ship screen's first window is Upgrades
    CURRENT_TAB = 0x68         # unsigned int


class Upgrades:                # size 0x2D0, derives FocusWindow             DWARF
    BOXES = 0x28               # vector<UpgradeBox*>
    REACTOR_BUTTON = 0x148     # ReactorButton (Button: hitbox +0x10, bHover +0x49)
    REACTOR_TEMP = 0x148 + 0x8C  # int: reactor bars bought but not yet applied
    # Upgrades::Close applies every pending level (ShipSystem::UpgradeSystem).     REA


class UpgradeBox:              # size 0x168                                  DWARF
    SIZE = 0x168
    SYSTEM = 0x00              # ShipSystem* (null for an empty slot)
    BLUEPRINT = 0x10           # const SystemBlueprint*: upgradeCosts at +0x80
    TEMP = 0x20                # int tempUpgrade: levels bought but not yet applied
    CURRENT_BUTTON = 0x28      # Button*: what UpgradeBox::MouseClick needs hovered. It then pays
                               #   upgradeCosts[level - 1] (level = bars + temp) and adds one
                               #   temp level, refusing at maxLevel                            REA
    BOX_BUTTON = 0xC8          # Button
    SUBSYSTEM = 0x158          # bool
    DUMMY = 0x159              # bool isDummy: empty slot


SYSTEM_BLUEPRINT_UPGRADE_COSTS = 0x80   # vector<int>                                    DWARF


class Store:                   # size 0x658, derives FocusWindow             DWARF
    SIZE = 0x658
    BOXES = 0x478              # vector<StoreBox*>: weapons, drones, systems, crew...
    ITEM_BOXES = 0x490         # vector<StoreBox*>: fuel, missiles, drone parts, hull repair
    PAGE2 = 0x640              # bool bShowPage2
    CONFIRM_BUY = 0x648        # StoreBox*: purchase waiting on the Yes/No dialog
    PAGE1_BUTTON = 0x68        # Button (GenericButton: hitbox +0x10, bActive +0x48, bHover +0x49)
    PAGE2_BUTTON = 0xF8        # Button
    CONFIRM_DIALOG = 0x188     # ConfirmWindow: FocusWindow (bOpen +0x8), yesButton +0x78, noButton +0x178
    CONFIRM_YES = 0x188 + 0x78  # TextButton (a GenericButton)
    SHOPPER = 0x4A8            # ShipManager* of the buyer
    SECTION_COUNT = 0x62C      # int: sections of boxes (3 boxes each)
    PAGE1_BOXES = 6            # Store::MouseMove: BOXES[0..5] are page 1, the rest page 2  REA
    # Store::MouseClick buys the hovered, active box; boxes that need a confirmation
    # open the ConfirmWindow (+0x188) instead.                                  REA


class StoreBox:                # size 0x140 (+ subclass fields)              DWARF
    SIZE = 0x168               # read enough for the largest subclass used (SystemStoreBox)
    BUTTON = 0x18              # Button: hitbox +0x10, bActive +0x48, bHover +0x49
    COST = 0xB0 + 0x30         # desc.cost
    COUNT = 0x110              # int: how many are left
    BLUEPRINT = 0x128          # const Blueprint*: name at +0x8
    REPAIR_ALL = 0x140         # RepairStoreBox: bool
    REPAIR_COST = 0x144        # RepairStoreBox: int


# Store box vtables (vptr = vtable + 0x10) identify the kind of box.         nm
STORE_BOX_VTABLES = {
    0x77AA20 + 0x10: "item",
    0x77AAC0 + 0x10: "repair",
    0x77AC00 + 0x10: "weapon",
    0x77A980 + 0x10: "drone",
    0x77A840 + 0x10: "augment",
    0x77AB60 + 0x10: "system",
    0x77A8E0 + 0x10: "crew",
}


class Sector:                  # size 0xE8                                   DWARF
    TYPE = 0x00                # int
    NEIGHBORS = 0x08           # vector<Sector*>
    LEVEL = 0x28               # int
    KIND = 0x30 + 0x80         # SectorDescription.type (std::string), e.g. "CIVILIAN"
    NAME = 0x30 + 0x88         # SectorDescription.name (TextString data)


class Location:                # size 0xE0                                   DWARF
    SIZE = 0xE0
    LOC = 0x00                 # Pointf: position on the sector map
    CONNECTED = 0x08           # vector<Location*>
    BEACON = 0x20              # bool: the sector exit                                       verified live
    KNOWN = 0x21               # bool
    VISITED = 0x24             # int
    DANGER_ZONE = 0x28         # bool: overtaken by the rebel fleet                          verified live
    NEBULA = 0x2A              # bool
    BOSS = 0x2B                # bool: the Flagship is here (Last Stand)
    EVENT = 0x30               # LocationEvent*; a store beacon has event->pStore set
                               #   (REA: StarMap::LocationHasStore)


class LocationEvent:           # size 0x708                                  DWARF
    SIZE = 0x708
    TEXT = 0x00                # TextString (data string at +0)
    SHIP = 0x10                # ShipEvent: present +0x0, hostile +0x288
    SHIP_PRESENT = 0x10 + 0x0
    SHIP_HOSTILE = 0x10 + 0x288
    STUFF = 0x340              # ResourceEvent: what the event gives or takes right away
    ENVIRONMENT = 0x4B8        # int
    STORE = 0x4C0              # bool
    REPAIR = 0x4CB             # bool
    P_STORE = 0x4D0            # Store*: non-null at a store beacon
    DAMAGE = 0x4D8             # vector<EventDamage> (0xC each: system +0x0, amount +0x4, effect +0x8)
    EVENT_NAME = 0x538         # std::string
    REWARD = 0x540             # ResourceEvent: auto-reward
    BOARDERS = 0x6B8           # BoardingEvent: type string +0x0, min +0x8, max +0xC, amount +0x10, breach +0x14
    CHOICES = 0x6D0            # vector<LocationEvent::Choice>
    REVEAL_MAP = 0x4C9         # bool                                                DWARF
    MODIFY_PURSUIT = 0x4CC     # int: the rebel fleet moves this many jumps closer (negative: held back)
    QUEST = 0x4F0              # std::string: a quest beacon is added                DWARF
    STATUS_EFFECTS = 0x4F8     # vector<StatusEffect> (0x10 each: type, system, amount, target)
    UNLOCK_SHIP = 0x6E8        # int (-1 none)
# StatusEffect.type (EventsParser::ProcessEvent): 1 loss, 2 limit, 3 divide, 4 clear; target 0 player, 1 enemy
STATUS_LOSS, STATUS_LIMIT, STATUS_DIVIDE, STATUS_CLEAR = 1, 2, 3, 4
    # When the player picks on-screen choice N, WorldManager::PauseLoop follows
    # lastLocationEvent->choices[N].event (CreateChoiceBox trims the list to what is shown).  REA


class EventChoice:             # LocationEvent::Choice, size 0x38            DWARF
    SIZE = 0x38
    EVENT = 0x00               # LocationEvent*: the outcome of picking this choice
    TEXT = 0x08                # TextString
    REQUIREMENT = 0x18         # ChoiceReq.object (std::string): equipment/crew needed; empty if none
    BLUE = 0x18 + 0x14         # ChoiceReq.blue: defaults to true; only meaningful with a requirement
    REQUIREMENT_LEVEL = 0x18 + 0x8   # ChoiceReq.min_level (e.g. Hacking level 3)
    HIDDEN_REWARD = 0x30       # bool


class ResourceEvent:           # size 0x178                                  DWARF
    SIZE = 0x178
    MISSILES = 0x00
    FUEL = 0x04
    DRONES = 0x08
    SCRAP = 0x0C
    CREW = 0x10
    CREW_TYPE = 0x28           # std::string
    WEAPON = 0x30              # const WeaponBlueprint*
    DRONE = 0x38               # const DroneBlueprint*
    AUGMENT = 0x40             # const AugmentBlueprint*
    STEAL = 0x154              # bool
    INTRUDERS = 0x155          # bool
    HULL_DAMAGE = 0x15C        # int
    UPGRADE_AMOUNT = 0x160     # int
    SYSTEM_ID = 0x148          # int: a system the event gives (-1 none)           DWARF
    UPGRADE_ID = 0x164         # int: system upgraded by UPGRADE_AMOUNT             DWARF
    CLONEABLE = 0x15           # bool: a lost crew member can come back from a clonebay
    REMOVE_ITEM = 0x170        # std::string


BLUEPRINT_COST = 0x10 + 0x30   # Blueprint.desc (+0x10) -> Description.cost (+0x30)       DWARF
