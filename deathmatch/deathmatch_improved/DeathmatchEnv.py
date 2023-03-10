import random
import time 
import numpy as np
import cv2


from gym import Env 
from gym.spaces import Discrete, Box

from vizdoom import * 
from vizdoom.vizdoom import GameVariable, ScreenResolution, ScreenFormat

AMMO_VARIABLES = [GameVariable.AMMO0, GameVariable.AMMO1, GameVariable.AMMO2, GameVariable.AMMO3, GameVariable.AMMO4, GameVariable.AMMO5, GameVariable.AMMO6, GameVariable.AMMO7, GameVariable.AMMO8, GameVariable.AMMO9]
WEAPON_VARIABLES = [GameVariable.WEAPON0, GameVariable.WEAPON1, GameVariable.WEAPON2, GameVariable.WEAPON3, GameVariable.WEAPON4, GameVariable.WEAPON5, GameVariable.WEAPON6, GameVariable.WEAPON7, GameVariable.WEAPON8, GameVariable.WEAPON9]

class DoomWithBots(Env):
    def __init__(self, render=bool(False), config='cig.cfg', n_bots=12):
        # inherit from Gym Env
        super().__init__()

        # Set up the game
        self.game = DoomGame()
        self.game.load_config(config)
        self.game.set_doom_map("map18")
        if render == True:
            self.game.set_window_visible(True)
        else:
            self.game.set_window_visible(False)
        self.game.add_game_args('-host 1 -deathmatch +viz_nocheat 0 +cl_run 1 +name AI +colorset 4 +sv_forcerespawn 1 +sv_respawnprotect 1 +sv_nocrouch 1 +sv_noexit 1')
        self.game.set_mode(Mode.PLAYER)
        self.game.set_console_enabled(True)
        self.game.set_screen_resolution(ScreenResolution.RES_320X240)
        self.game.set_screen_format(ScreenFormat.RGB24)
        # Start the game 
        self.game.init()
        
        # Create the action space and observation space
        h, w, c = self.frame_processor(self.game.get_state().screen_buffer).shape
        self.observation_space = Box(low=0, high=255, shape=(h,w,c), dtype=np.uint8) 
        self.action_space = Discrete(7)
        

        # Reset stats and initialize them
        self.n_bots = n_bots
        self.total_rew = self.last_damage_dealt = self.deaths = self.last_frags = self.last_armor = 0
        self.last_health = 100
        self.last_x, self.last_y = self.player_position()
        self.ammo_state = self.get_ammo_state()
        self.weapon_state = self.get_weapon_state()

        self.rewards_stats = {
            'frag': 0,
            'damage': 0,
            'ammo': 0,
            'health': 0,
            'armor': 0,
            'distance': 0,
        }

        self.reward_factor_frag = 1.0
        self.reward_factor_damage = 0.01

        self.reward_factor_distance = 0.00005
        self.penalty_factor_distance = 0.0025
        self.reward_threshold_distance = 5.0

        self.reward_factor_ammo_increment = 0.02
        self.reward_factor_ammo_decrement = -0.02

        self.reward_factor_health_increment = 0.02
        self.reward_factor_health_decrement = -0.01
        self.reward_factor_armor_increment = 0.01

        self.game.send_game_command('removebots')
        for i in range(self.n_bots):
            self.game.send_game_command('addbot')
        
        self.empty_frame = np.zeros(self.observation_space.shape, dtype=np.uint8)
        self.state = self.empty_frame

    def step(self, action):
        # Specify action and take step 
        actions = np.identity(7)

        self.game.make_action(actions[action], 4)
        reward = self.shape_rewards(initial_reward=0)                
        self.total_rew += reward  
        

        self.respawn_if_dead()
        done = self.game.is_episode_finished()
        self.state = self.get_frame(done)

        return self.state, reward, done, {'frags': self.last_frags}

    def reset(self):
        self.game.new_episode()
        self.print_state()
        self.state = self.get_frame()

        self.last_x, self.last_y = self.player_position()
        self.last_health = 100
        self.last_armor = self.last_frags = self.total_rew = self.deaths = 0

        # Reset reward stats
        for k in self.rewards_stats.keys():
            self.rewards_stats[k] = 0

        self.game.send_game_command('removebots')
        for i in range(self.n_bots):
            self.game.send_game_command('addbot')

        return self.state

    def get_frame(self, done: bool = False):
        return self.frame_processor(
            self.game.get_state().screen_buffer) if not done else self.empty_frame

    def shape_rewards(self, initial_reward: float):
        frag_reward = self.compute_frag_reward()
        damage_reward = self.compute_damage_reward()
        ammo_reward = self.compute_ammo_reward()
        health_reward = self.compute_health_reward()
        armor_reward = self.compute_armor_reward()
        distance_reward = self.compute_distance_reward(*self.player_position())

        return initial_reward + frag_reward + damage_reward + ammo_reward + health_reward + armor_reward + distance_reward

    def compute_distance_reward(self, x, y):
        dx = self.last_x - x
        dy = self.last_y - y

        self.last_x = x
        self.last_y = y

        distance = np.sqrt(dx ** 2 + dy ** 2)
        d = distance - self.reward_threshold_distance

        if d > 0:
            reward = 0.0005
        else:
            reward = -0.0005

        self.log_reward_stat('distance', reward)

        return reward

    def compute_frag_reward(self):
        frags = self.game.get_game_variable(GameVariable.FRAGCOUNT)
        reward = self.reward_factor_frag * (frags - self.last_frags)

        self.last_frags = frags
        self.log_reward_stat('frag', reward)

        return reward

    def compute_damage_reward(self):
        damage_dealt = self.game.get_game_variable(GameVariable.DAMAGECOUNT)
        reward = self.reward_factor_damage * (damage_dealt - self.last_damage_dealt)

        self.last_damage_dealt = damage_dealt
        self.log_reward_stat('damage', reward)

        return reward

    def compute_health_reward(self):
        health = max(self.game.get_game_variable(GameVariable.HEALTH), 0)

        health_reward = self.reward_factor_health_increment * max(0, health - self.last_health)
        health_penalty = self.reward_factor_health_decrement * min(0, health - self.last_health)
        reward = health_reward - health_penalty

        self.last_health = health
        self.log_reward_stat('health', reward)

        return reward

    def compute_armor_reward(self):
        armor = self.game.get_game_variable(GameVariable.ARMOR)
        reward = self.reward_factor_armor_increment * max(0, armor - self.last_armor)
        self.last_armor = armor
        self.log_reward_stat('armor', reward)

        return reward

    def compute_ammo_reward(self):
        self.weapon_state = self.get_weapon_state()

        new_ammo_state = self.get_ammo_state()
        ammo_diffs = (new_ammo_state - self.ammo_state) * self.weapon_state
        ammo_reward = self.reward_factor_ammo_increment * max(0, np.sum(ammo_diffs))
        ammo_penalty = self.reward_factor_ammo_decrement * min(0, np.sum(ammo_diffs))
        reward = ammo_reward - ammo_penalty
        self.ammo_state = new_ammo_state
        self.log_reward_stat('ammo', reward)

        return reward

    def player_position(self):
        # needs viz_nocheat 0
        return self.game.get_game_variable(GameVariable.POSITION_X), self.game.get_game_variable(
            GameVariable.POSITION_Y)

    def get_ammo_state(self):
        ammo_state = np.zeros(10)

        for i in range(10):
            ammo_state[i] = self.game.get_game_variable(AMMO_VARIABLES[i])

        return ammo_state

    def get_weapon_state(self):
        # ten unique weapons
        weapon_state = np.zeros(10)

        for i in range(10):
            weapon_state[i] = self.game.get_game_variable(WEAPON_VARIABLES[i])

        return weapon_state

    def log_reward_stat(self, kind: str, reward: float):
        self.rewards_stats[kind] += reward

    def reset_player(self):
        self.last_health = 100
        self.last_armor = 0
        self.game.respawn_player()
        self.last_x, self.last_y = self.player_position()
        self.ammo_state = self.get_ammo_state()

    def auto_change_weapon(self):
        # Change weapons to one with ammo
        possible_weapons = np.flatnonzero(self.ammo_state * self.weapon_state)
        possible_weapon = possible_weapons[-1] if len(possible_weapons) > 0 else None

        current_selection = self.game.get_game_variable(GameVariable.SELECTED_WEAPON)
        new_selection = possible_weapon if possible_weapon != current_selection else None

        return new_selection

    def respawn_if_dead(self):
        if not self.game.is_episode_finished():
            # Check if player is dead
            if self.game.is_player_dead():
                self.deaths += 1
                self.reset_player()

    def print_state(self):
        server_state = self.game.get_server_state()
        player_scores = list(zip(
            server_state.players_names,
            server_state.players_frags,
            server_state.players_in_game))
        player_scores = sorted(player_scores, key=lambda tup: tup[1])

        print('*** DEATHMATCH RESULTS ***')
        for player_name, player_score, player_ingame in player_scores:
            if player_ingame:
                print(f' - {player_name}: {player_score}')

    def frame_processor(self, observation):
        state = cv2.resize(observation[50:-50, :], None, fx=.5, fy=.5, interpolation=cv2.INTER_CUBIC)
        return state

    def close(self): 
        self.game.close()
