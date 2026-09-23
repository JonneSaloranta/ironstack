"""Translation catalog for seeded *content*, not UI chrome — the
system stretch library and routines (apps.stretching.migrations 0002,
from seed_data/stretches.json) are stored in canonical English, so the
stored value itself must never be translated. This module exists solely
so `makemessages` extracts these exact strings into the `.po` catalog;
nothing here is ever imported or executed for its return value — see
apps.exercises.i18n_content and docs/ARCHITECTURE.md
"Internationalization" for the full pattern. Templates render all of
these through `|translate_content` (apps.core.templatetags.core_extras),
since names like "Child's Pose" and multi-line instructions are exactly
the kind of value `{% trans var %}` is awkward with.

Keep this file in sync with seed_data/stretches.json — StretchingSeedTests
fails if a seeded string is missing here.
"""

from django.utils.translation import gettext_lazy as _

# Stretch names.
STRETCH_NAMES = [
    _("Standing Quad Stretch"),
    _("Kneeling Hip Flexor Stretch"),
    _("Couch Stretch"),
    _("Standing Hamstring Stretch"),
    _("Lying Hamstring Stretch"),
    _("Seated Forward Fold"),
    _("Wide-Leg Forward Fold"),
    _("Figure-Four Stretch"),
    _("Pigeon Pose"),
    _("Knee-to-Chest Stretch"),
    _("Wall Calf Stretch"),
    _("Deep Squat Hold"),
    _("Downward Dog"),
    _("Doorway Chest Stretch"),
    _("Cross-Body Shoulder Stretch"),
    _("Overhead Triceps Stretch"),
    _("Biceps Wall Stretch"),
    _("Child's Pose"),
    _("Standing Side Bend"),
    _("Thread the Needle"),
    _("Cobra Stretch"),
    _("Supine Spinal Twist"),
    _("Upper Back Stretch"),
    _("Neck Side Stretch"),
    _("Wrist Flexor Stretch"),
    _("Wrist Extensor Stretch"),
    _("Cat-Cow"),
    _("Arm Circles"),
    _("Hip Circles"),
    _("Torso Rotations"),
    _("Leg Swings"),
    _("World's Greatest Stretch"),
]

# Routine names and descriptions.
ROUTINE_TEXTS = [
    _("Lower Body Cool-Down"),
    _("Five static stretches for the legs and hips after a lower-body workout."),
    _("Upper Body Cool-Down"),
    _("Chest, shoulders, arms and neck after an upper-body workout."),
    _("Full Body Stretch"),
    _("About ten minutes of static stretching from head to toe."),
    _("Morning Mobility"),
    _("Gentle dynamic movements to wake the body up, or as a warm-up."),
]

# Stretch instructions — split into one literal per line to stay under
# the line-length limit (Python concatenates adjacent literals).
STRETCH_INSTRUCTIONS = [
    _(
        "- Stand tall and hold onto a wall or chair for balance.\n"
        "- Bend one knee and grab that ankle behind you.\n"
        "- Pull the heel gently towards your glutes, keeping the knees together and "
        "the hips pushed slightly forward.\n"
        "- Hold, then switch sides."
    ),
    _(
        "- Kneel on one knee with the other foot flat on the floor in front of you.\n"
        "- Tuck the pelvis under and squeeze the glute of the kneeling leg.\n"
        "- Shift your hips forward until you feel a stretch at the front of the hip.\n"
        "- Keep the torso upright; hold, then switch sides."
    ),
    _(
        "- Kneel with your back to a wall or couch and slide one shin up against it.\n"
        "- Step the other foot forward into a lunge.\n"
        "- Slowly raise your torso upright while squeezing the glute of the back leg.\n"
        "- Hold, then switch sides."
    ),
    _(
        "- Place one heel on a low step or bench with the leg straight.\n"
        "- Keep your back flat and hinge forward from the hips.\n"
        "- Stop when you feel a stretch along the back of the thigh.\n"
        "- Hold, then switch sides."
    ),
    _(
        "- Lie on your back with both legs extended.\n"
        "- Raise one leg, holding behind the thigh or calf (or use a towel around the foot).\n"
        "- Keep the knee as straight as is comfortable and pull the leg gently towards you.\n"
        "- Hold, then switch sides."
    ),
    _(
        "- Sit with both legs straight out in front of you.\n"
        "- Lengthen your spine, then hinge forward from the hips.\n"
        "- Reach towards your feet without forcing it.\n"
        "- Breathe slowly and relax a little deeper on each exhale."
    ),
    _(
        "- Stand with your feet wide apart and toes pointing forward.\n"
        "- Hinge forward from the hips with a flat back.\n"
        "- Let your hands rest on the floor or your shins.\n"
        "- Relax your head and neck, and come up slowly."
    ),
    _(
        "- Lie on your back with both knees bent.\n"
        "- Cross one ankle over the opposite knee.\n"
        "- Pull the lower leg towards your chest by holding behind the thigh.\n"
        "- Hold, then switch sides."
    ),
    _(
        "- From hands and knees, bring one knee forward behind the same wrist.\n"
        "- Extend the other leg straight back.\n"
        "- Square your hips and lower your torso over the front leg as far as is comfortable.\n"
        "- Hold, then switch sides."
    ),
    _(
        "- Lie on your back with both legs extended.\n"
        "- Pull one knee towards your chest with both hands.\n"
        "- Keep the other leg relaxed on the floor.\n"
        "- Hold, then switch sides."
    ),
    _(
        "- Stand facing a wall with your hands on it.\n"
        "- Step one foot back, keeping that leg straight and the heel on the floor.\n"
        "- Lean into the wall until you feel a stretch in the calf.\n"
        "- Hold, then switch sides."
    ),
    _(
        "- Stand with your feet a little wider than hip-width.\n"
        "- Sit down into the deepest squat you can hold with your heels on the floor.\n"
        "- Use your elbows to gently press the knees outwards.\n"
        "- Keep your chest up and breathe."
    ),
    _(
        "- Start on hands and knees, then lift your hips up and back.\n"
        "- Straighten your arms and legs as far as is comfortable to form an inverted V.\n"
        "- Press your heels towards the floor and your chest towards your thighs.\n"
        "- Keep breathing slowly."
    ),
    _(
        "- Stand in a doorway and place your forearms on the frame at shoulder height.\n"
        "- Step one foot forward.\n"
        "- Lean gently forward until you feel a stretch across the chest.\n"
        "- Keep your shoulders down and away from your ears."
    ),
    _(
        "- Bring one arm straight across your chest.\n"
        "- Use the other hand to press it gently towards your body, above the elbow.\n"
        "- Keep the shoulder down and relaxed.\n"
        "- Hold, then switch sides."
    ),
    _(
        "- Raise one arm overhead and bend the elbow so the hand drops behind your head.\n"
        "- Use the other hand to gently push the elbow down and back.\n"
        "- Keep your head up and your ribs down.\n"
        "- Hold, then switch sides."
    ),
    _(
        "- Stand side-on to a wall and place your palm on it slightly behind you, "
        "arm straight at shoulder height.\n"
        "- Slowly turn your body away from the wall.\n"
        "- Stop when you feel a stretch along the front of the arm.\n"
        "- Hold, then switch sides."
    ),
    _(
        "- Kneel and sit back on your heels with the knees apart.\n"
        "- Walk your hands forward and lower your chest towards the floor.\n"
        "- Reach your arms long and let your forehead rest down.\n"
        "- Breathe into your back."
    ),
    _(
        "- Stand with your feet hip-width apart.\n"
        "- Raise one arm overhead.\n"
        "- Lean to the opposite side, reaching up and over without twisting.\n"
        "- Hold, then switch sides."
    ),
    _(
        "- Start on hands and knees.\n"
        "- Slide one arm under your body towards the opposite side, palm up.\n"
        "- Lower that shoulder and the side of your head towards the floor.\n"
        "- Hold, then switch sides."
    ),
    _(
        "- Lie face down with your hands under your shoulders.\n"
        "- Press up slowly, lifting your chest while the hips stay on the floor.\n"
        "- Keep your shoulders away from your ears.\n"
        "- Only go as high as your lower back is comfortable."
    ),
    _(
        "- Lie on your back with your arms out to the sides.\n"
        "- Bend one knee and let it fall across your body to the opposite side.\n"
        "- Keep both shoulders on the floor and turn your head the other way.\n"
        "- Hold, then switch sides."
    ),
    _(
        "- Clasp your hands in front of you at shoulder height.\n"
        "- Round your upper back and push your hands away.\n"
        "- Let your head drop gently between your arms.\n"
        "- Feel the stretch between your shoulder blades."
    ),
    _(
        "- Sit or stand tall with your shoulders relaxed.\n"
        "- Tilt your ear towards one shoulder.\n"
        "- Optionally rest the hand on that side gently on your head, without pulling.\n"
        "- Hold, then switch sides."
    ),
    _(
        "- Extend one arm straight in front of you, palm up.\n"
        "- Use the other hand to gently pull the fingers down and back.\n"
        "- Keep the elbow straight.\n"
        "- Hold, then switch sides."
    ),
    _(
        "- Extend one arm straight in front of you, palm down.\n"
        "- Use the other hand to gently bend the wrist down towards you.\n"
        "- Keep the elbow straight.\n"
        "- Hold, then switch sides."
    ),
    _(
        "- Start on hands and knees.\n"
        "- Inhale as you drop your belly and lift your chest and tailbone.\n"
        "- Exhale as you round your back and tuck your chin.\n"
        "- Move slowly with your breath."
    ),
    _(
        "- Stand tall with your arms out to the sides.\n"
        "- Draw small circles, gradually making them bigger.\n"
        "- Switch direction halfway through.\n"
        "- Keep the movement smooth and controlled."
    ),
    _(
        "- Stand with your hands on your hips and feet hip-width apart.\n"
        "- Draw big, slow circles with your hips.\n"
        "- Switch direction halfway through.\n"
        "- Keep your upper body still."
    ),
    _(
        "- Stand with your feet shoulder-width apart and arms relaxed.\n"
        "- Rotate your upper body from side to side, letting the arms swing.\n"
        "- Keep your hips facing forward.\n"
        "- Gradually increase the range of motion."
    ),
    _(
        "- Stand side-on to a wall and hold it for balance.\n"
        "- Swing the outside leg forward and back in a controlled arc.\n"
        "- Let the range grow gradually with each swing.\n"
        "- Then switch sides."
    ),
    _(
        "- Step into a long lunge and place both hands on the floor inside the front foot.\n"
        "- Drop the inside elbow towards the front ankle.\n"
        "- Rotate and reach that same arm up towards the ceiling.\n"
        "- Return and repeat slowly, then switch sides."
    ),
]
