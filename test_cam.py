import cv2
print('Testing ANY')
cap = cv2.VideoCapture(0, cv2.CAP_ANY)
if cap.isOpened():
    ok, frame = cap.read()
    print('ANY ok:', ok)
    cap.release()
else:
    print('ANY failed to open')

print('Testing MSMF')
cap = cv2.VideoCapture(0, cv2.CAP_MSMF)
if cap.isOpened():
    ok, frame = cap.read()
    print('MSMF ok:', ok)
    cap.release()
else:
    print('MSMF failed to open')

print('Testing DSHOW')
cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
if cap.isOpened():
    ok, frame = cap.read()
    print('DSHOW ok:', ok)
    cap.release()
else:
    print('DSHOW failed to open')
