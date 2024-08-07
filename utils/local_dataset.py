import os, pathlib, time
import threading 

import random, json, copy, configparser
from collections import defaultdict, deque
import json, zarr


dirs = ['TS_'+str(i)+'_'+str(j) for i in range(1,100) for j in range(1,10)]
dir2id = {j:i for i,j in enumerate(dirs)}
dir_set = set(dirs)


# define a wrapper function
def threaded(fn):
    def wrapper(*args, **kwargs):
        thread = threading.Thread(target=fn, args=args, kwargs=kwargs)
        thread.start()
        return thread
    return wrapper


class LocalDataset:
    def __init__(self, local_file_path: str=None, config_path: str=None):
        self.root = local_file_path
        with open(config_path) as f:
            self.config_file = json.load(f)
        
        # output
        self.proteins = defaultdict(int) # {'ribosome': 38, ...}
        self.tomograms = defaultdict(set)  #{'TS_1_1':{'ribosome', ...}, ...}
        self.tomos_per_person = defaultdict(set) #{'john.doe':{'TS_1_1',...},...} 
        self.tomos_pickers = defaultdict(set)   #{'Test_1_1': {john.doe,...}, ...}
        self.num_per_person_ordered = dict() # {'Tom':5, 'Julie':3, ...}
        
        # hidden variables for updating candidate recomendations 
        self._all = set([i for i in range(len(dirs))])
        self._tomos_done = set()   # labeled at least by 2 people, {0, 1, 2}
        self._tomos_one_pick = set() # labeled only by 1 person, {3,4,5,...} 
        self._candidate_dict = defaultdict() # {1:1, 2:0, ...}
        self._prepicks = set(['slab-picking', 
                             'pytom-template-match', 
                             'relion-refinement', 
                             'prepick', 
                             'ArtiaX', 
                             'default']
                            ) 

        xdata = []
        colors = dict()
        for po in self.config_file["pickable_objects"]:
            xdata.append(po["name"])
            colors[po["name"]] = po["color"]

        self._im_dataset = {'name': xdata, 
                           'count': [], 
                           'colors': colors
                          }

    def _reset(self):
        self.proteins = defaultdict(int) 
        self._tomos_one_pick = set() #may remove some elems, thereofore, empty before each check

        xdata = []
        colors = dict()
        for po in self.config_file["pickable_objects"]:
            xdata.append(po["name"])
            colors[po["name"]] = po["color"]
            
        self._im_dataset = {'name': xdata, 
                           'count': [], 
                           'colors': colors
                          }
        
    
    def refresh(self):
        self._reset()
        self._update_tomo_sts()

    
    @threaded
    def _walk_dir(self, args):
        r, s, e = args
        for dir in dirs[s:e]:
            dir_path = r + dir +'/Picks'
            if os.path.exists(dir_path):
                for json_file in pathlib.Path(dir_path).glob('*.json'):
                    try:
                        contents = json.load(open(json_file))
                        if 'user_id' in contents and contents['user_id'] not in self._prepicks:
                            if 'pickable_object_name' in contents and \
                            'run_name' in contents and contents['run_name'] in dir_set and \
                            'points' in contents and contents['points'] and len(contents['points']):
                                    self.proteins[contents['pickable_object_name']] += len(contents['points'])
                                    self.tomos_per_person[contents['user_id']].add(contents['run_name'])
                                    self.tomograms[contents['run_name']].add(contents['pickable_object_name']) 
                                    self.tomos_pickers[contents['run_name']].add(contents['user_id'])
                    except: 
                        pass
                        

    def _update_tomo_sts(self):
        start = time.time() 
        seg = round(len(dirs)/6)
        args1 = (self.root, 0, seg)
        args2 = (self.root, seg, seg*2)
        args3 = (self.root, seg*2, seg*3)
        args4 = (self.root, seg*3, seg*4)
        args5 = (self.root, seg*4, seg*5)
        args6 = (self.root, seg*5, len(dirs))

        t1 = self._walk_dir(args1)
        t2 = self._walk_dir(args2)
        t3 = self._walk_dir(args3)
        t4 = self._walk_dir(args4)
        t5 = self._walk_dir(args5)
        t6 = self._walk_dir(args6)

        t1.join()
        t2.join()
        t3.join()
        t4.join()
        t5.join()
        t6.join()
        print(f'{time.time()-start} s to check all files')
        
        for tomo,pickers in self.tomos_pickers.items():
            if len(pickers) >= 2:
                self._tomos_done.add(dir2id[tomo])
            elif len(pickers) == 1:
                self._tomos_one_pick.add(dir2id[tomo])

        self.num_per_person_ordered = dict(sorted(self.tomos_per_person.items(), key=lambda item: len(item[1]), reverse=True))

        
    def _update_candidates(self, n, random_sampling=True):
        # remove candidates that should not be considered any more
        _candidate_dict = defaultdict() 
        for candidate in self._candidate_dict.keys():
            if candidate in self._tomos_done:
                continue
            _candidate_dict[candidate] = self._candidate_dict[candidate]
        self._candidate_dict = _candidate_dict
        
        # add candidates that have been picked once
        if len(self._candidate_dict) < n:
            for i in self._tomos_one_pick:
                self._candidate_dict[i] = 1
                if len(self._candidate_dict) == n:
                    break

        # add candidates that have not been picked yet 
        if len(self._candidate_dict) < n:
            residuals = self._all - self._tomos_done - self._tomos_one_pick
            residuals = deque(residuals)     
            while residuals and len(self._candidate_dict) < n:
                if random_sampling:
                    new_id = random.randint(0,len(residuals))
                    self._candidate_dict[residuals[new_id]] = 0
                    del residuals[new_id]       
                else:
                    new_candidate = residuals.popleft()
                    self._candidate_dict[new_candidate] = 0
        

    def candidates(self, n: int, random_sampling=True) -> dict:
        self._candidate_dict = {k:0 for k in range(n)} if not random_sampling else {k:0 for k in random.sample(range(len(dirs)), n)}
        self._update_candidates(n, random_sampling) 
        return {k: v for k, v in sorted(self._candidate_dict.items(), key=lambda x: x[1], reverse=True)}
    
    
    def fig_data(self):
        image_dataset = copy.deepcopy(self._im_dataset)
        proteins = copy.deepcopy(self.proteins)
        for name in image_dataset['name']:
            image_dataset['count'].append(proteins[name])
                         
        image_dataset['colors'] = {k:'rgba'+str(tuple(v)) for k,v in image_dataset['colors'].items()}
        return image_dataset



COUNTER_FILE_PATH = None
local_dataset = None
def get_local_dataset(LOCAL_FILE_PATH=None, COPICK_TEMPLATE_PATH=None, COUNTER_CHECKPOINT_PATH=None):
    global local_dataset
    global COUNTER_FILE_PATH
    if not local_dataset:
        if not LOCAL_FILE_PATH or not COPICK_TEMPLATE_PATH:
            config = configparser.ConfigParser()
            config.read(os.path.join(os.getcwd(), "config.ini"))
        
        if not LOCAL_FILE_PATH:
            LOCAL_FILE_PATH = '%s' % config['local_picks']['PICK_FILE_PATH'] + 'ExperimentRuns/'
        if not COPICK_TEMPLATE_PATH:
            COPICK_TEMPLATE_PATH = '%s' % config['copick_template']['COPICK_TEMPLATE_PATH']
        
        local_dataset = LocalDataset(local_file_path=LOCAL_FILE_PATH, config_path=COPICK_TEMPLATE_PATH)
    
    if not COUNTER_FILE_PATH:
        if not COUNTER_CHECKPOINT_PATH:
            config = configparser.ConfigParser()
            config.read(os.path.join(os.getcwd(), "config.ini"))

        if not COUNTER_CHECKPOINT_PATH:
            COUNTER_FILE_PATH = '%s' % config['counter_checkpoint']['COUNTER_FILE_PATH']
        else:
             COUNTER_FILE_PATH = COUNTER_CHECKPOINT_PATH
           

get_local_dataset()