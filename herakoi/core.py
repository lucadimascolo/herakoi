import argparse

import numpy as np
import re

import cv2
import mediapipe as mp

import tkinter
import tkinter.filedialog as filedialog

import mido
import rtmidi

import time
import sys

vlims_ = (40,127) 
flims_ = (48, 95) # C2-B5

root = tkinter.Tk()
scrw = root.winfo_screenwidth()
scrh = root.winfo_screenheight()
root.withdraw()

# Convert BGR image into HSV
# -------------------------------------
class gethsv:
  def __init__(self,inp):
    self.bgr = cv2.imread(inp)
    self.hsv = cv2.cvtColor(self.bgr,cv2.COLOR_BGR2HSV)
    
    self.mono = np.logical_and((self.bgr[...,0]==self.bgr[...,1]).all(),
                               (self.bgr[...,1]==self.bgr[...,2]).all())

    if self.mono:
      self.hsv[...,0] = self.hsv[...,2].copy()
    else:
      self.hsv[self.hsv[...,0]>150.00,0] = 0.00

    self.h, self.w, _ = self.bgr.shape

# Convert key name
# Ported from the pretty-midi package
# -------------------------------------
def nametopitch(name):
    pitch_map = {'C': 0, 'D': 2, 'E':  4, 'F':  5, 'G': 7, 'A': 9, 'B': 11}
    accen_map = {'#': 1,  '': 0, 'b': -1, '!': -1}

    try:
      match = re.match(r'^(?P<n>[A-Ga-g])(?P<off>[#b!]?)(?P<oct>[+-]?\d+)$',name)

      pitch = match.group('n').upper()
      offset = accen_map[match.group('off')]
      octave = int(match.group('oct'))
    except:
      raise ValueError('Improper note format: {0}'.format(name))

    return 12*(octave+1)+pitch_map[pitch]+offset

# Build the herakoi player
# =====================================
class start:
  def __init__(self,image=None,mode='single',port={},video=0,box=2,**kwargs):
  
    if image is None:
      tkinter.Tk().withdraw()
      imgpath = filedialog.askopenfilenames()
        
      if len(imgpath)<1: sys.exit(1)

      imgpath = imgpath[0]
    else:
      imgpath = sys.argv[1]

    if mode not in ['single','adaptive','scan','party']:
      raise NotImplementedError('"{0}" mode is unknown'.format(mode))
    elif mode=='party':
      raise NotImplementedError('"party mode" not yet implemented')

    self.valname = 'herakoi'

  # Build virtual MIDI port
  # -------------------------------------
    midinew = rtmidi.MidiOut()

    if midinew.get_ports(): midinew.open_port(port.get('value',0))
    else: midinew.open_virtual_port(port.get('name',self.valname))

    self.midiout = mido.open_output(port.get('name',self.valname),virtual=True)

  # Start capture from webcam
  # -------------------------------------
    self.opvideo = cv2.VideoCapture(video)
    self.opmusic = gethsv(imgpath)

    cv2.namedWindow('imframe',cv2.WINDOW_NORMAL)

    self.mphands = mp.solutions.hands
    self.mpdraws = mp.solutions.drawing_utils
    self.mpstyle = mp.solutions.drawing_styles

    self.opindex = 8
    self.opthumb = 4
    
    if mode=='adaptive':
      self.oppatch = None
    else:
      self.oppatch = np.minimum(self.opmusic.w,self.opmusic.h)
      self.oppatch = int(np.clip((box/100)*self.oppatch,2,None))
    
    self.opcolor = {'Left': (0,255,  0), 
                   'Right': (0,255,255)}

    if 'volume' in kwargs:
      vlims = (np.interp(kwargs['volume'],(0,100),(0,127)),127)
    else: vlims = vlims_

    if 'notes' in kwargs:
      flims = (nametopitch(kwargs['notes'][0]),
               nametopitch(kwargs['notes'][1]))
    else: flims = flims_

    self.run(mode,vlims=vlims,flims=flims,**kwargs)

# Convert H and B to note and loudness
# =====================================
  def getmex(self,posx,box,vlims=vlims_,flims=flims_):
    def getval(img,clip):
      val = np.median(img[np.clip(posx[1]-box[1]//2,0,self.opmusic.h-1):np.clip(posx[1]+box[1]//2,0,self.opmusic.h-1),
                          np.clip(posx[0]-box[0]//2,0,self.opmusic.w-1):np.clip(posx[0]+box[0]//2,0,self.opmusic.w-1)])
      vmidi = 0 if np.isnan(val) else val
      vmidi = int(np.interp(vmidi,(img.min(),img.max()),clip))
      return vmidi

    fout = getval(self.opmusic.hsv[...,0],flims)
    vout = getval(self.opmusic.hsv[...,2],vlims)

    return fout, vout

# Draw and return hand markers position
# =====================================
  def posndraw(self,frame,marks,label,draw=True):
    if draw: self.mpdraws.draw_landmarks(frame,marks,self.mphands.HAND_CONNECTIONS,None)

    point = marks.landmark[self.opindex]
    posix = [int(point.x*frame.shape[1]),
             int(point.y*frame.shape[0]),np.abs(point.z)*300]

    if draw and self.oppatch is not None: 
      cv2.circle(frame,(posix[0],posix[1]),np.clip(int(posix[2]),2,None),self.opcolor[label],-1)

    return posix

# Rescale image according to input
# =====================================
  def rescale(self,image):
    if image.shape[1]>image.shape[0]:
      if (self.opmusic.w<self.opmusic.h) or \
         (self.opmusic.h/self.opmusic.w)>(image.shape[0]/image.shape[1]):
        wk = (self.opmusic.w/self.opmusic.h)*image.shape[0]
        wi = int(0.50*(image.shape[1]-wk))     
        return image[:,wi:-wi]
      else: 
        hk = (self.opmusic.h/self.opmusic.w)*image.shape[1]
        hi = int(0.50*(image.shape[0]-hk))
        return image[hi:-hi,:]
    else:
      return image

# Single-user mode
# =====================================
  def run(self,mode='single',vlims=vlims_,flims=flims_,**kwargs):
    ophands = self.mphands.Hands(max_num_hands=2 if mode=='scan' else 1)

    onmusic = False

    pxshift = kwargs.get('shift',2)
    pxshift = (pxshift/100)*np.minimum(self.opmusic.w,self.opmusic.h)

    toctime = kwargs.get('toc',0.05)
    offtime = kwargs.get('off',0.05)
    tictime = time.time()

    while True:
      _, opframe = self.opvideo.read()

      opframe = self.rescale(opframe)

      opframe = cv2.flip(opframe,1)
      imframe = cv2.cvtColor(opframe,cv2.COLOR_BGR2RGB)

      immusic = self.opmusic.bgr.copy()
      imhands = ophands.process(imframe)

      bhmidif = None
      bhmidiv = None

      if imhands.multi_hand_landmarks:
        if mode=='scan':
          pxmusic = [0,0,50]
          for mi, immarks in enumerate(imhands.multi_hand_landmarks):
            imlabel = imhands.multi_handedness[mi].classification[0].label
            
            self.mpdraws.draw_landmarks(immusic,immarks,self.mphands.HAND_CONNECTIONS,None)
            self.mpdraws.draw_landmarks(opframe,immarks,self.mphands.HAND_CONNECTIONS,None)

            px, py, _ = self.posndraw(immusic,immarks,imlabel,False)

            if imlabel=='Right': pxmusic[0] = px
            if imlabel=='Left':  pxmusic[1] = py

          cv2.circle(immusic,(pxmusic[0],pxmusic[1]),pxmusic[2],(255,255,255),-1)

        else:
          for mi, immarks in enumerate(imhands.multi_hand_landmarks):
            imlabel = imhands.multi_handedness[mi].classification[0].label

            _       = self.posndraw(opframe,immarks,imlabel,True)

            if self.oppatch is None:
              pxindex = immarks.landmark[self.opindex]
              pxthumb = immarks.landmark[self.opthumb]
              pxpatch = [int(np.abs(pxindex.x-pxthumb.x)*immusic.shape[1]),
                         int(np.abs(pxindex.y-pxthumb.y)*immusic.shape[0])]

              _ = self.posndraw(immusic,immarks,imlabel,True)
              pxmusic = [0.50*(pxindex.x+pxthumb.x),0.50*(pxindex.y+pxthumb.y),0.50*(pxindex.z+pxthumb.z)]
              pxmusic = [int(pxmusic[0]*immusic.shape[1]),
                         int(pxmusic[1]*immusic.shape[0]),int(pxmusic[2]*300)]

              cv2.rectangle(immusic,(int(pxthumb.x*immusic.shape[1]),int(pxthumb.y*immusic.shape[0])),
                                    (int(pxindex.x*immusic.shape[1]),int(pxindex.y*immusic.shape[0])),self.opcolor[imlabel],1)
              cv2.circle(immusic,(pxmusic[0],pxmusic[1]),2,self.opcolor[imlabel],-1)
 
            else:
              pxmusic = self.posndraw(immusic,immarks,imlabel,True)
              pxpatch = [self.oppatch,self.oppatch]

            if (mode in ['single','adaptive'] and imlabel=='Left') or (mode=='party'):
              bhmidif, bhmidiv = self.getmex(pxmusic,pxpatch,vlims,flims)
            
            if (mode in ['single','adaptive'] and imlabel=='Right'):
              rhmidif, rhmidiv = self.getmex(pxmusic,pxpatch,vlims,flims)

              bhmidif = rhmidif if bhmidif is None else int(0.50*(rhmidif+bhmidif))
              bhmidiv = rhmidiv if bhmidiv is None else int(0.50*(rhmidiv+bhmidiv))

        if mode in ['single','adaptive']:
          if (bhmidif is not None) and (bhmidiv is not None):
            if time.time()-tictime>toctime and not onmusic:
              self.midiout.send(mido.Message('note_on',channel=8,note=bhmidif,velocity=bhmidiv))
              pxmusicold = pxmusic
              onmusic = True

            if time.time()-tictime>toctime+offtime and np.hypot(pxmusicold[0]-pxmusic[0],pxmusicold[1]-pxmusic[1])>pxshift:
              self.midiout.send(mido.Message('note_off',channel=8,note=bhmidif))
              self.panic(); onmusic = False
              
              tic = time.time()
          else: self.panic()
      else: self.panic()

      cv2.imshow('imframe',opframe)
      cv2.imshow('immusic',immusic)
      
      imgw = cv2.getWindowImageRect('imframe')[2]

      cv2.moveWindow('imframe',scrw-imgw,0)

      if cv2.waitKey(1) & 0xFF == ord('q'): break

    self.opvideo.release()
    cv2.destroyAllWindows()


# Turn off all MIDI notes
# =====================================
  def panic(self):
    for note in range(0,127):
      self.midiout.send(mido.Message('note_off',channel=8,note=note))
    self.midiout.reset()