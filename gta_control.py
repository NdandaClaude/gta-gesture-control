# ============================================================================
# GTA San Andreas - Contrôle Gestuel par Webcam (MVP v2)
# ============================================================================
# Ce script détecte :
#   - La direction de la tête/nez (via Pose) → déplacement WASD
#   - Les coups de poing (via Hands)    → frappe LCTRL
#
# Installation des dépendances :
#   pip install opencv-python mediapipe pydirectinput
#
# Modèles requis (téléchargement automatique au premier lancement) :
#   hand_landmarker.task   — détection des mains
#   pose_landmarker.task   — détection du corps
#
# Utilisation :
#   1. Lancer GTA San Andreas.
#   2. Exécuter ce script : python gta_control.py
#   3. Placer la fenêtre du jeu au premier plan.
#   4. Se tenir droit devant la webcam (calibration auto 3 secondes).
#   5. Tourner la tête pour se déplacer, lever les mains pour frapper.
#   6. Appuyer sur 'q' dans la fenêtre OpenCV pour quitter.
# ============================================================================

import os
import time
import urllib.request

import cv2
import numpy as np
import mediapipe as mp
import pydirectinput

from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python.vision import (
    HandLandmarker,
    HandLandmarkerOptions,
    PoseLandmarker,
    PoseLandmarkerOptions,
    RunningMode,
)

# ---------------------------------------------------------------------------
# Configuration — Combat
# ---------------------------------------------------------------------------

# Seuil vertical : si le bout du majeur (coordonnée y normalisée 0-1,
# 0 = haut de l'image) passe en dessous de cette valeur, on déclenche le coup.
PUNCH_Y_THRESHOLD = 0.5

# Délai minimum entre deux coups (en secondes) pour éviter le spam.
PUNCH_COOLDOWN = 0.15

# Touches de combat (LCTRL = FIRE dans GTA SA).
PUNCH_KEY_LEFT = "ctrlleft"
PUNCH_KEY_RIGHT = "ctrlleft"

# Durée de maintien de la touche (en secondes) pour que GTA détecte l'appui.
KEY_HOLD_DURATION = 0.05

# Indice du landmark MediaPipe pour le bout du majeur (landmark n°12).
MIDDLE_FINGER_TIP = 12

# ---------------------------------------------------------------------------
# Configuration — Déplacement (direction de la tête)
# ---------------------------------------------------------------------------

# Seuils de déplacement (en coordonnées normalisées).
# Si le nez s'éloigne de plus de ce seuil par rapport à la
# position calibrée, on active le déplacement.
HEAD_X_THRESHOLD = 0.04   # Tourner la tête gauche / droite
HEAD_Y_THRESHOLD = 0.03   # Pencher la tête avant / arrière

# Touches de déplacement (WASD).
KEY_FORWARD  = "w"
KEY_BACKWARD = "s"
KEY_LEFT     = "a"
KEY_RIGHT    = "d"

# Facteur de lissage (0 = pas de lissage, 1 = pas de mouvement).
# Plus la valeur est élevée, plus le mouvement est stable.
SMOOTH_FACTOR = 0.7

# Landmark Pose pour le nez (indice MediaPipe).
NOSE = 0

# ---------------------------------------------------------------------------
# Chemins des modèles
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

HAND_MODEL_PATH = os.path.join(SCRIPT_DIR, "hand_landmarker.task")
HAND_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"
)

POSE_MODEL_PATH = os.path.join(SCRIPT_DIR, "pose_landmarker.task")
POSE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task"
)

# ---------------------------------------------------------------------------
# Téléchargement automatique des modèles si absents
# ---------------------------------------------------------------------------

for model_path, model_url, name in [
    (HAND_MODEL_PATH, HAND_MODEL_URL, "Hand Landmarker"),
    (POSE_MODEL_PATH, POSE_MODEL_URL, "Pose Landmarker"),
]:
    if not os.path.isfile(model_path):
        print(f"[INFO] Téléchargement de {name} depuis {model_url} ...")
        urllib.request.urlretrieve(model_url, model_path)
        print(f"[INFO] {name} téléchargé avec succès.")

# ---------------------------------------------------------------------------
# Connexions de la main (pour dessiner le squelette manuellement)
# ---------------------------------------------------------------------------

HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),        # Pouce
    (0, 5), (5, 6), (6, 7), (7, 8),        # Index
    (0, 9), (9, 10), (10, 11), (11, 12),   # Majeur
    (0, 13), (13, 14), (14, 15), (15, 16), # Annulaire
    (0, 17), (17, 18), (18, 19), (19, 20), # Auriculaire
    (5, 9), (9, 13), (13, 17),             # Paume
]

# ---------------------------------------------------------------------------
# Initialisation MediaPipe — Hands + Pose
# ---------------------------------------------------------------------------

hand_landmarker = HandLandmarker.create_from_options(HandLandmarkerOptions(
    base_options=BaseOptions(model_asset_path=HAND_MODEL_PATH),
    running_mode=RunningMode.VIDEO,
    num_hands=2,
    min_hand_detection_confidence=0.5,
    min_tracking_confidence=0.4,
))

pose_landmarker = PoseLandmarker.create_from_options(PoseLandmarkerOptions(
    base_options=BaseOptions(model_asset_path=POSE_MODEL_PATH),
    running_mode=RunningMode.VIDEO,
    num_poses=1,
    min_pose_detection_confidence=0.5,
    min_tracking_confidence=0.4,
))

# ---------------------------------------------------------------------------
# Initialisation de la capture vidéo
# ---------------------------------------------------------------------------

cap = cv2.VideoCapture(0)

if not cap.isOpened():
    print("[ERREUR] Impossible d'ouvrir la webcam.")
    exit(1)

print("[INFO] Webcam ouverte avec succès.")
print("[INFO] === CONTRÔLES ===")
print(f"[INFO] Tourner la tête à gauche/droite → A/D")
print(f"[INFO] Pencher la tête en avant/arrière → W/S")
print(f"[INFO] Lever les mains au-dessus du seuil → LCTRL (frapper)")
print(f"[INFO] Appuyez sur 'q' dans la fenêtre vidéo pour quitter.")
print("[INFO] Calibration dans 3 secondes — restez droit devant la caméra !\n")

# Désactiver la pause intégrée de pydirectinput pour un contrôle manuel.
pydirectinput.PAUSE = 0.0

# ---------------------------------------------------------------------------
# Variables d'état
# ---------------------------------------------------------------------------

# Combat.
last_punch_time = {"Left": 0.0, "Right": 0.0}

# Déplacement — touches actuellement enfoncées.
keys_held = {KEY_FORWARD: False, KEY_BACKWARD: False, KEY_LEFT: False,
             KEY_RIGHT: False}

# Position lissée du nez.
smooth_nx = None
smooth_ny = None

# Calibration : position neutre du nez.
calibrated = False
calib_center_x = 0.5   # Milieu par défaut
calib_center_y = 0.4
calib_frames = []
CALIB_DURATION = 3.0    # Secondes de calibration
calib_start_time = time.time()

# Compteur de timestamps pour le mode VIDEO de MediaPipe.
frame_timestamp_ms = 0

# ---------------------------------------------------------------------------
# Fonctions utilitaires
# ---------------------------------------------------------------------------

# Couleurs par main (BGR).
HAND_COLORS = {
    "Left":  (255, 100, 0),   # Bleu
    "Right": (0, 100, 255),   # Orange
}

def draw_hand_landmarks(image, landmarks, label="Right"):
    """Dessine les landmarks et connexions de la main sur l'image."""
    color = HAND_COLORS.get(label, (0, 255, 0))
    h, w, _ = image.shape
    points = []
    for lm in landmarks:
        px, py = int(lm.x * w), int(lm.y * h)
        points.append((px, py))
        cv2.circle(image, (px, py), 4, color, -1)
    for a, b in HAND_CONNECTIONS:
        if a < len(points) and b < len(points):
            cv2.line(image, points[a], points[b], color, 2)


def set_key(key, should_press):
    """Enfonce ou relâche une touche de déplacement (sans spam)."""
    if should_press and not keys_held[key]:
        pydirectinput.keyDown(key)
        keys_held[key] = True
    elif not should_press and keys_held[key]:
        pydirectinput.keyUp(key)
        keys_held[key] = False


def release_all_movement():
    """Relâche toutes les touches de déplacement."""
    for key in keys_held:
        if keys_held[key]:
            pydirectinput.keyUp(key)
            keys_held[key] = False


def get_nose_position(pose_landmarks):
    """Retourne (x, y) du nez."""
    nose = pose_landmarks[NOSE]
    return nose.x, nose.y

# ---------------------------------------------------------------------------
# Boucle principale
# ---------------------------------------------------------------------------

try:
    while cap.isOpened():
        success, frame = cap.read()
        if not success:
            continue

        # Retourner l'image horizontalement (effet miroir, plus intuitif).
        frame = cv2.flip(frame, 1)
        h, w, _ = frame.shape

        # Convertir BGR → RGB pour MediaPipe.
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)

        # Incrémenter le timestamp (en ms) ; doit être strictement croissant.
        frame_timestamp_ms += 33  # ~30 FPS

        # ===================================================================
        # POSE — Détection du corps pour le déplacement
        # ===================================================================
        pose_results = pose_landmarker.detect_for_video(mp_image, frame_timestamp_ms)

        if pose_results.pose_landmarks:
            pose_lms = pose_results.pose_landmarks[0]
            nx, ny = get_nose_position(pose_lms)

            # Dessiner le nez sur l'image.
            nose_px, nose_py = int(nx * w), int(ny * h)
            cv2.circle(frame, (nose_px, nose_py), 10, (255, 255, 255), -1)

            # Lissage de la position du nez.
            if smooth_nx is None:
                smooth_nx, smooth_ny = nx, ny
            else:
                smooth_nx = SMOOTH_FACTOR * smooth_nx + (1 - SMOOTH_FACTOR) * nx
                smooth_ny = SMOOTH_FACTOR * smooth_ny + (1 - SMOOTH_FACTOR) * ny

            # --- Phase de calibration ---
            if not calibrated:
                elapsed = time.time() - calib_start_time
                calib_frames.append((nx, ny))
                remaining = max(0, CALIB_DURATION - elapsed)
                cv2.putText(frame, f"CALIBRATION... {remaining:.1f}s",
                            (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 3)
                cv2.putText(frame, "Regardez DROIT devant la camera",
                            (10, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

                if elapsed >= CALIB_DURATION:
                    xs = [p[0] for p in calib_frames]
                    ys = [p[1] for p in calib_frames]
                    calib_center_x = np.mean(xs)
                    calib_center_y = np.mean(ys)
                    calibrated = True
                    print(f"[INFO] Calibration terminée ! Nez au centre: ({calib_center_x:.3f}, {calib_center_y:.3f})")
                    print("[INFO] Tournez la tête pour contrôler CJ !")
            else:
                # --- Déplacement basé sur la direction de la tête (lissé) ---
                dx = smooth_nx - calib_center_x   # Positif = droite
                dy = smooth_ny - calib_center_y   # Positif = bas

                # Gauche / Droite.
                set_key(KEY_LEFT, dx < -HEAD_X_THRESHOLD)
                set_key(KEY_RIGHT, dx > HEAD_X_THRESHOLD)

                # Avant / Arrière (pencher la tête en avant = nez descend = dy positif).
                set_key(KEY_FORWARD, dy > HEAD_Y_THRESHOLD)
                set_key(KEY_BACKWARD, dy < -HEAD_Y_THRESHOLD)

                # Dessiner le point de calibration (croix rouge).
                cal_px = int(calib_center_x * w)
                cal_py = int(calib_center_y * h)
                cv2.drawMarker(frame, (cal_px, cal_py), (0, 0, 255),
                               cv2.MARKER_CROSS, 30, 2)

                # Dessiner la zone morte.
                zone_left  = int((calib_center_x - HEAD_X_THRESHOLD) * w)
                zone_right = int((calib_center_x + HEAD_X_THRESHOLD) * w)
                zone_up    = int((calib_center_y - HEAD_Y_THRESHOLD) * h)
                zone_down  = int((calib_center_y + HEAD_Y_THRESHOLD) * h)
                cv2.rectangle(frame, (zone_left, zone_up), (zone_right, zone_down),
                              (0, 0, 255), 2)

                # Ligne entre la position calibrée et le nez actuel.
                smooth_px = int(smooth_nx * w)
                smooth_py = int(smooth_ny * h)
                cv2.line(frame, (cal_px, cal_py), (smooth_px, smooth_py), (0, 255, 255), 2)

                # Afficher la direction active.
                direction = ""
                if keys_held[KEY_FORWARD]:  direction += "W "
                if keys_held[KEY_BACKWARD]: direction += "S "
                if keys_held[KEY_LEFT]:     direction += "A "
                if keys_held[KEY_RIGHT]:    direction += "D "
                if not direction:
                    direction = "NEUTRE"
                cv2.putText(frame, f"DIR: {direction}",
                            (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 3)
                cv2.putText(frame, f"dx={dx:+.3f} dy={dy:+.3f}",
                            (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 2)
        else:
            # Pas de pose détectée → relâcher les touches de déplacement.
            if calibrated:
                release_all_movement()

        # ===================================================================
        # HANDS — Détection des mains pour les coups de poing
        # ===================================================================
        # On crée une deuxième image car le timestamp doit être identique
        # mais on ne peut pas réutiliser le même objet.
        mp_image2 = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
        hand_results = hand_landmarker.detect_for_video(mp_image2, frame_timestamp_ms)

        if hand_results.hand_landmarks:
            for idx, hand_lms in enumerate(hand_results.hand_landmarks):
                # Déterminer quelle main (correction miroir).
                raw_label = "Right"
                if idx < len(hand_results.handedness):
                    raw_label = hand_results.handedness[idx][0].category_name
                label = "Right" if raw_label == "Left" else "Left"

                # Dessiner le squelette.
                draw_hand_landmarks(frame, hand_lms, label)

                # Récupérer le bout du majeur.
                finger_tip = hand_lms[MIDDLE_FINGER_TIP]
                tip_y = finger_tip.y
                cx_h = int(finger_tip.x * w)
                cy_h = int(finger_tip.y * h)

                color = HAND_COLORS.get(label, (0, 255, 255))
                cv2.putText(frame, f"{label} y={tip_y:.2f}",
                            (cx_h + 10, cy_h - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

                # Détection du coup de poing.
                now = time.time()
                if tip_y < PUNCH_Y_THRESHOLD and (now - last_punch_time[label]) > PUNCH_COOLDOWN:
                    key = PUNCH_KEY_LEFT if label == "Left" else PUNCH_KEY_RIGHT
                    pydirectinput.keyDown(key)
                    time.sleep(KEY_HOLD_DURATION)
                    pydirectinput.keyUp(key)
                    last_punch_time[label] = now
                    print(f"[PUNCH {label.upper()}] Coup envoyé ! (y={tip_y:.2f})")
                    cv2.circle(frame, (cx_h, cy_h), 20, (0, 0, 255), -1)

        # Dessiner la ligne de seuil pour les coups.
        threshold_line_y = int(PUNCH_Y_THRESHOLD * h)
        cv2.line(frame, (0, threshold_line_y), (w, threshold_line_y), (0, 255, 0), 2)
        cv2.putText(frame, f"Seuil punch (y={PUNCH_Y_THRESHOLD})",
                    (10, threshold_line_y - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

        # Afficher le flux vidéo.
        cv2.imshow("GTA Control - Gesture Detection", frame)

        # Quitter avec la touche 'q'.
        if cv2.waitKey(1) & 0xFF == ord("q"):
            print("[INFO] Arrêt demandé par l'utilisateur.")
            break

except KeyboardInterrupt:
    print("\n[INFO] Interruption clavier détectée.")

finally:
    # Relâcher toutes les touches avant de quitter.
    release_all_movement()
    hand_landmarker.close()
    pose_landmarker.close()
    cap.release()
    cv2.destroyAllWindows()
    print("[INFO] Ressources libérées. Au revoir !")
