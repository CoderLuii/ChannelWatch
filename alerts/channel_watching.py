�
    ���g��  �                   �  � 	 d dl Z d dlZd dlZd dlmZmZmZ d dlmZ d dlZddl	m
Z
 ddlmZ ddlmZ ddlmZ dd	lmZ d
dlmZmZmZ d
dlmZmZmZmZmZmZmZ d
dlm Z  d
dl!m"Z"  e j#        �   �         Z$ G d� de
e�  �        Z%dS )�    N)�Dict�Any�Optional)�datetime�   )�	BaseAlert)�SessionManager)�AlertFormatter)�CleanupMixin)�StreamTracker�   )�log�LOG_STANDARD�LOG_VERBOSE)�extract_channel_number�extract_channel_name�extract_device_name�extract_ip_address�extract_resolution�extract_source_from_session_id�is_valid_ip_address)�ChannelInfoProvider)�ProgramInfoProviderc                   �  � e Zd Z	 dZdZd� Zd� Zd� Zdede	ee
f         defd	�Zdede	ee
f         defd
�Zdede	ee
f         defd�Zde	ee
f         dedefd�Zde	ee
f         defd�Zdeddfd�Zdd�Zdd�Zd� Zdededefd�ZdS )�ChannelWatchingAlertzChannel-Watchingz)Notifications when someone is watching TVc                 �J  � 	 t          j        | |�  �         t          j        | �  �         t          �   �         | _        ddlm} |�                    d�  �        | _        t          |�                    dd�  �        �  �        | _
        |�                    dd�  �        | _        |�                    d|�                    d	d
�  �        �  �        �                    �   �         d
k    }|�                    d|�                    dd
�  �        �  �        �                    �   �         d
k    }|�                    d|�                    dd
�  �        �  �        �                    �   �         d
k    }|�                    d|�                    dd
�  �        �  �        �                    �   �         d
k    }|�                    d|�                    dd
�  �        �  �        �                    �   �         d
k    }|�                    d|�                    dd
�  �        �  �        �                    �   �         d
k    }t          ||||||ddd���  �        | _        t          | _        d| _        t          |�                    dd�  �        �  �        | _        t          |�                    dd�  �        �  �        | _        t)          | j        | j
        | j        ��  �        | _        t-          | j        | j
        �  �        | _        t1          | j        | j
        | j        | j        ��  �        | _        |�                    dd
�  �        �                    �   �         d
k    | _        |�                    d|�                    dd
�  �        �  �        �                    �   �         d
k    | _        |�                    d|�                    d d!�  �        �  �        �                    �   �         �                    �   �         | _        | j        d"vr&t?          d#| j        � d$�t@          �%�  �         d!| _        | �!                    dd&d�'�  �         d S )(Nr   )�environ�CHANNELS_DVR_HOST�CHANNELS_DVR_PORT�8089�TZzAmerica/New_York�CW_CHANNEL_NAME�CHANNEL_NAME�TRUE�CW_CHANNEL_NUMBER�CHANNEL_NUMBER�CW_PROGRAM_NAME�PROGRAM_NAME�CW_DEVICE_NAME�DEVICE_NAME�CW_DEVICE_IP�DEVICE_IP_ADDRESS�CW_STREAM_SOURCE�STREAM_SOURCETu   📺 )�show_channel_name�show_channel_number�show_program_name�show_device_name�show_ip�show_source�	use_emoji�title_prefix)�config�   �CHANNEL_CACHE_TTL�86400�PROGRAM_CACHE_TTL)�	cache_ttl�STREAM_COUNT�CW_IMAGE_SOURCE�IMAGE_SOURCE�Channel)r@   �ProgramzInvalid CW_IMAGE_SOURCE value: z. Using 'Channel' as default.��leveli  )�enabled�interval�auto_cleanup)"r   �__init__r   r	   �session_manager�osr   �get�host�int�port�timezone�upperr
   �alert_formatter�time�time_module�alert_cooldown�channel_cache_ttl�program_cache_ttlr   �channel_providerr   �stream_trackerr   �program_provider�stream_count_enabled�program_name_enabled�strip�
capitalize�image_sourcer   r   �configure_cleanup)	�self�notification_managerr   r/   r0   r1   r2   r3   r4   s	            �alerts/channel_watching.pyrG   zChannelWatchingAlert.__init__'   s�  � �4���4�!5�6�6�6���d�#�#�#�  .�/�/��� 	�������K�K� 3�4�4��	�����$7��@�@�A�A��	����D�*<�=�=��� $�K�K�(9�7�;�;�~�W]�;^�;^�_�_�e�e�g�g�kq�q��%�k�k�*=�w�{�{�K[�]c�?d�?d�e�e�k�k�m�m�qw�w��#�K�K�(9�7�;�;�~�W]�;^�;^�_�_�e�e�g�g�kq�q��"�;�;�'7����]�TZ�9[�9[�\�\�b�b�d�d�hn�n���+�+�n�g�k�k�:M�v�.V�.V�W�W�]�]�_�_�ci�i���k�k�"4�g�k�k�/�SY�6Z�6Z�[�[�a�a�c�c�gm�m��  .�!2�#6�!2� 0��&��#�	6
� 	6
� 	 � 	 � 	 ���  ���  ��� "%�W�[�[�1D�g�%N�%N�!O�!O���!$�W�[�[�1D�g�%N�%N�!O�!O��� !4�D�I�t�y�TX�Tj� k� k� k��� ,�D�I�t�y�A�A��� !4�D�I�t�y�$�-�cg�cy� z� z� z��� %,�K�K���$G�$G�$M�$M�$O�$O�SY�$Y��!�$+�K�K�0A�7�;�;�~�_e�Cf�Cf�$g�$g�$m�$m�$o�$o�sy�$y��!� $�K�K�(9�7�;�;�~�W`�;a�;a�b�b�h�h�j�j�u�u�w�w�����$:�:�:��b�$�2C�b�b�b�jv�w�w�w�w� )�D�� 	������ 	� 	
� 	
� 	
� 	
� 	
�    c                 �t   � 	 | j         �                    �   �          | j        r| �                    �   �          d S d S �N)rV   �cache_channelsrZ   �_cache_program_info�r_   s    ra   �_cache_channelsz$ChannelWatchingAlert._cache_channelsp   sI   � �L���,�,�.�.�.� �$� 	'��$�$�&�&�&�&�&�	'� 	'rb   c                 �:   � 	 | j         �                    �   �          d S rd   )rX   �cache_program_datarg   s    ra   rf   z(ChannelWatchingAlert._cache_program_infox   s    � �L���0�0�2�2�2�2�2rb   �
event_type�
event_data�returnc                 �0   � 	 | �                     ||�  �        S rd   )�_is_watching_event)r_   rk   rl   s      ra   �_should_handle_eventz)ChannelWatchingAlert._should_handle_event}   s   � �	� �&�&�z�:�>�>�>rb   c                 �l  � 	 t           5  	 |�                    dd�  �        }|�                    dd�  �        }t          |�  �        }|s't          d|� �t          ��  �         	 d d d �  �         dS t          |�  �        }t          |�  �        }|r|n|}|s't          d|� �t          ��  �         	 d d d �  �         dS d|� d	|� �}	t          d
|	� d|� d|� d|� d�	t          ��  �         d}
d}| j        r4| j        �	                    ||�  �        }
| j        �
                    �   �         }| j        �                    |	�  �        r(t          d|	� d�t          ��  �         	 d d d �  �         dS | j        �                    | j        |	| j        �  �        s	 d d d �  �         dS | j        �                    |	�  �         	 |
r^| j        rW| �                    ||�  �        sAt          d|� �t$          ��  �         	 | j        �                    |	�  �         d d d �  �         dS | �                    ||	�  �        }|| j        �                    |	�  �         cd d d �  �         S # | j        �                    |	�  �         w xY w# t*          $ r(}t          d|� ��  �         Y d }~d d d �  �         dS d }~ww xY w# 1 swxY w Y   d S )N�Value� �Namez!Channel number missing in value: rB   Fz?Could not determine device identifier (name or IP) from value: �ch�-zProcessing event for z (SessionID: z, DeviceName: z, IPAddress: �)r   zSkipping duplicate event for z - already processing�Total Streams: zError processing event: )�
event_lockrJ   r   r   r   r   r   rY   rW   �process_activity�get_stream_countrH   �is_event_processingrP   �should_send_notificationrS   �mark_event_processing�_is_new_sessionr   �complete_event_processing�_process_watching_event�	Exception)r_   rk   rl   �value�
session_id�channel_number�device_name�
ip_address�device_identifier�tracking_key�stream_changed�current_count�success�es                 ra   �_handle_eventz"ChannelWatchingAlert._handle_event�   s�  � �.�� F	� F	�E�"���w��3�3��'�^�^�F�B�7�7�
�!7��!>�!>��%� !��C�E�C�C�;�W�W�W�W� �F	� F	� F	� F	� F	� F	� F	� F	� 2�%�8�8��/��6�6�
� 4?�$N�K�K�J�!� )� "��b�[`�b�b�ju�v�v�v�v� "�3F	� F	� F	� F	� F	� F	� F	� F	�8  I�N�H�H�5F�H�H���  I�L�  I�  I�z�  I�  I�al�  I�  I�  |F�  I�  I�  I�  Q\�  ]�  ]�  ]�  ]� "'�� !���,� K�%)�%8�%I�%I�%�Q[�%\�%\�N�$(�$7�$H�$H�$J�$J�M� �'�;�;�L�I�I� !��[��[�[�[�cn�o�o�o�o� �UF	� F	� F	� F	� F	� F	� F	� F	�Z �+�D�D��,�$��+�-� -� !� !�cF	� F	� F	� F	� F	� F	� F	� F	�h �$�:�:�<�H�H�H�Q� &� %�$�*C� %�D�L`�L`�ak�m{�L|�L|� %��=�m�=�=�\�R�R�R�R�$� �(�B�B�<�P�P�P�GF	� F	� F	� F	� F	� F	� F	� F	�| #�:�:�:�|�T�T�G�"� �(�B�B�<�P�P�P�GF	� F	� F	� F	� F	� F	� F	� F	��F �(�B�B�<�P�P�P�P������ � � ��2�q�2�2�3�3�3��u�u�u�MF	� F	� F	� F	� F	� F	� F	� F	�����H����IF	� F	� F	� F	���� F	� F	� F	� F	� F	� F	sr   �J)�AI4�/?I4�<BI4�(&I4�I4�78I�0I4�I�.I4�I1�1I4�4
J&�>J!�J)�!J&�&J)�)J-�0J-c                 �  � 	 |dk    sd|vs|�                     d�  �        sdS |�                     dd�  �        }t          d|� d|d d�         � d�t          �	�  �         d
|v p+d|�                    �   �         v od|�                    �   �         v S )Nzactivities.setrr   Frs   zChecking event: z
 - Value: �2   �...rB   zWatching ch�channel�watching)rJ   r   r   �lower)r_   rk   rl   r�   s       ra   ro   z'ChannelWatchingAlert._is_watching_event�   s�   � �	� �*�*�*��:�%�%����w�'�'� &��5� ���w��+�+�� 	�D�z�D�D�U�3�B�3�Z�D�D�D�K�X�X�X�X� �U�"� I��%�+�+�-�-�'�G�J�%�+�+�-�-�,G�	
rb   r�   c                 ��	  � 	 	 |�                     dd�  �        }|�                     dd�  �        }t          |�  �        }|sdS t          |�  �        }|sd}| j        �                    |�  �        r�| j        �                    |�  �        }|�                     di �  �        }|�                     d�  �        }	|	|k    r;| j        �                    |||��  �         t          d	|� d
|� d�t          �  �         dS | j	        �
                    �   �         }
g }| j        j        �                    �   �         D ]R\  }}||k    rG|�                     di �  �        }|�                     dd�  �        }||k    r|�                    |�  �         �S|D ]�}| j        �                    |�  �        }|r�|�                     di �  �        }t          d|�                     dd�  �        � d|�                     dd�  �        � d|�                     dd�  �        � d|�                     dd�  �        � d|�                     dd�  �        � �
t          ��  �         | j        �                    |�  �         ��i }||d<   t!          |�  �        }|r||d<   ||d<   t#          |�  �        }d}|rA|�                    d�  �        }t'          |�  �        dk    r|d         }t)          |�  �        r|}|r|n|}|r|nd|d<   t+          |�  �        }|r|nd|d<   t-          |�  �        }|r|nd|d<   |�                     d�  �        r|�                     d�  �        s�t/          |�  �        }| j        �                    |�  �        }|rV|�                     d�  �        s |�                     d�  �        r|d         |d<   |�                     d�  �        r|d         |d<   n|�                     d�  �        sd |d<   | j        r| j        �                    �   �         |d!<   | j        ri| j        �                    |�  �        }|rM|d"         |d#<   |�                     d$�  �        r-| j         d%k    r|d$         |d&<   n| j         d'k    r|d$         |d&<   | �!                    |�  �        }|r9| j        �"                    |�  �         | j        �                    |||��  �         d(S dS # tF          $ r}t          d)|� ��  �         Y d }~dS d }~ww xY w)*Nrt   rs   rr   F�Unknown device�channel_info�number)r�   r�   zStill watching channel z on z - update only�device�Exited �name�Unknown� (Ch�) - Device: �N/A�, IP: �ip�
, Source: �sourcerB   rv   r   ������
Unknown IPzUnknown resolution�
resolution�Unknown source�logo_urlzUnknown Channel�stream_count�title�program_title�icon_urlrA   �program_icon_urlr@   Tz!Error processing watching event: )$rJ   r   r   rH   �has_session�get_session�add_sessionr   r   rR   rQ   �active_sessions�items�appendr   �remove_sessionr   r   �split�lenr   r   r   �strrV   �get_channel_inforY   rW   r{   rZ   rX   �get_current_programr]   �_send_alert�record_notificationr�   )r_   rl   r�   r�   r�   r�   r�   �session_data�old_channel_info�old_channel_number�current_time�device_sessions�active_session_id�active_channel_info�active_device�old_session_id�old_session_datar�   �channel_name�ip_from_val�ip_from_name�
name_parts�	last_part�preferred_ipr�   r�   �channel_number_str�provider_info�program_infor�   r�   s                                  ra   r�   z,ChannelWatchingAlert._process_watching_event�   s<  � �S�[	�#�����3�3�J��N�N�7�B�/�/�E� 4�E�:�:�N�!� ��u� .�e�4�4�K�� /�.�� �#�/�/�
�;�;� !�#�3�?�?�
�K�K��#/�#3�#3�N�B�#G�#G� �%5�%9�%9�(�%C�%C�"� &��7�7��(�4�4�"�%5�%1� 5� � � �
 �a�.�a�a�k�a�a�a�cn�o�o�o� �5�  �+�0�0�2�2�L� �O� 48�3G�3W�3]�3]�3_�3_� B� B�/�!�<�$�
�2�2�*6�*:�*:�>�2�*N�*N�'�$7�$;�$;�H�b�$I�$I�M� %��3�3�'�.�.�/@�A�A�A�� #2� H� H��#'�#7�#C�#C�N�#S�#S� �#� H�'7�';�';�N�B�'O�'O�$��r�"2�"6�"6�v�i�"H�"H� r� r�.�2�2�8�B�?�?�r� r�M]�Ma�Ma�bj�kp�Mq�Mq�r� r�/�3�3�D��?�?�r� r�K[�K_�K_�`h�in�Ko�Ko�r� r� +�	� � � � �(�7�7��G�G�G�� �L�%3�L��"� 0��6�6�L�� 4�'3��V�$� &1�L��"� -�U�3�3�K� �L�� 4�(�.�.�s�3�3���
�O�O�a�'�'�",�R�.�i�,�Y�7�7� 4�*3�<� +6�G�;�;�<�L�1=�!O���<�L��� ,�E�2�2�J�7A�)[���G[�L��&� 4�J�?�?�F�/5�%K�V�V�;K�L��"�  �#�#�F�+�+� A�<�3C�3C�J�3O�3O� A�%(��%8�%8�"� $� 5� F� F�GY� Z� Z�� � 	A�'�+�+�F�3�3� E��8I�8I�&�8Q�8Q� E�/<�V�/D��V�,�$�(�(��4�4� M�3@��3L��Z�0�� (�+�+�F�3�3� A�/@��V�,� �(� V�/3�/B�/S�/S�/U�/U��^�,� �(� X�#�4�H�H��X�X��� 	X�4@��4I�L��1� $�'�'�
�3�3� X��,�	�9�9�?K�J�?W�L�);�<�<�!�.�)�;�;�?K�J�?W�L�);�<� �&�&�|�4�4�G�� ��$�8�8��F�F�F� �$�0�0��!-�!-� 1� � � � �t��5��� 	� 	� 	��7�A�7�7�8�8�8��5�5�5�5�5�����	���s%   �=R: �B1R: �5OR: �:
S!�S�S!r�   c           
      �  � 	 	 |�                     dd�  �        }|�                     dd�  �        }|�                     dd�  �        }|�                     dd�  �        }|�                     d	d
�  �        }|�                     d�  �        }d }| j        re| j        �                    |�  �        }|r0t	          d|� d|�                     d�  �        � �t
          ��  �         nt	          d|� �t
          ��  �         d|� d|� d|� �}	|d
k    r|	d|� �z  }	|dk    r|	d|� �z  }	t	          |	t          ��  �         | j        r|�t	          d|� �t          ��  �         d}
|�                     dd�  �        }d}|r|�                     d�  �        r|d         }| j        dk    r |r|n|}
t	          d|
� �t
          ��  �         n|r|n|}
t	          d|
� �t
          ��  �         ||d�}|dk    r||d<   |||
d�}|r| j        r|d         |d<   | j        r|�||d<   t	          d |� d!|� d"|r|�                     d�  �        nd#� �t
          ��  �         | j	        �
                    ||�$�  �        }| �                    |d         |d%         |�                     d&�  �        �'�  �        }|S # t          $ r$}t	          d(|� �t          ��  �         Y d }~d)S d }~ww xY w)*Nr�   rs   r�   r�   r�   r�   r�   r�   r�   r�   r�   z Found program information for Chz: r�   rB   z#No program information found for Chz	Watching r�   r�   r�   r�   rx   r�   r�   r@   zUsing channel image: zUsing program image: )r�   r�   r�   )r�   r�   r�   r�   zFormatting alert with channel: z
, device: z, program: r�   )r�   �device_info�message�	image_url)r�   r�   r�   zError sending alert: F)rJ   rZ   rX   r�   r   r   r   rY   r]   rP   �format_channel_alert�
send_alertr�   )r_   r�   r�   r�   r�   r�   r�   r�   r�   �log_messager�   �channel_logo_url�program_image_urlr�   �alert_channel_info�formatted_alert�resultr�   s                     ra   r�   z ChannelWatchingAlert._send_alert�  s)  � �	�4x	� *�-�-�h��;�;�N�'�+�+�F�I�>�>�L� '�*�*�8�5E�F�F�K�%�)�)�$��=�=�J�!�%�%�h�0@�A�A�F� (�+�+�N�;�;�L�  �L��(� c�#�4�H�H��X�X��  � c��h�>�h�h�\�M]�M]�^e�Mf�Mf�h�h�p{�|�|�|�|�|��N�n�N�N�Va�b�b�b�b�
 b�l�a�a��a�a�T_�a�a�K� �)�)�)��4�F�4�4�4�� �\�)�)��4�
�4�4�4�� ��<�0�0�0�0� �(� J�\�-E��4�l�4�4�L�I�I�I�I�
 �I�  ,�/�/�
�B�?�?�� !#��� =�� 0� 0�� <� <� =�$0��$<�!� � �I�-�-�0@�W�,�,�FW�	��7�I�7�7�{�K�K�K�K�K� 2C�X�-�-�HX�	��7�I�7�7�{�K�K�K�K� $� �� �K� �\�)�)�,6��L�)� )�$�%�"� "�� � L�� 9� L�6B�7�6K�"�?�3� �(� B�\�-E�5A�"�>�2� �  Y�,�  Y�  Y�+�  Y�  Y�  @L�  cW�bn�br�br�sz�b{�b{�b{�  RW�  Y�  Y�!�#� #� #� #� #�2�G�G�/�'� H� � �O� �_�_�%�g�.�'�	�2�)�-�-�k�:�:� %� � �F� �M��� 	� 	� 	��+��+�+�<�@�@�@�@��5�5�5�5�5�����	���s   �JJ �
K�#K�Kr�   Nc           	      �  � 	 	 t          d|d d�         � d�t          ��  �         | j        �                    |�  �        }|�r�|�                    di �  �        }|�                    dd�  �        }|�                    dd	�  �        }d
|�                    dd�  �        � d|�                    dd	�  �        � d|�                    dd�  �        � �}|�                    dd�  �        }|dk    r|dk    r|d|� �z  }|�                    dd�  �        }|dk    r|dk    r|d|� �z  }t          |t
          ��  �         | j        r�|�                    dd�  �        }	t          d|	� d|d d�         � d�t          ��  �         | j        �                    i |�  �         | j        �	                    �   �         }
t          d|
� �t
          ��  �         | j        �
                    |�  �         t          d|d d�         � d�t          ��  �         d S t          d|d d�         � d�t          ��  �         d S # t          $ r$}t          d|� �t
          ��  �         Y d }~d S d }~ww xY w)Nz!Processing end event for session �   r�   rB   r�   r�   zUnknown channelr�   rs   r�   r�   r�   r�   r�   r�   r�   r�   r�   r�   r�   r�   r�   zRemoving stream for device z
, session zStream ended - Total Streams: zRemoved session z... from active sessionszNo session data found for zError processing end event: )r   r   rH   r�   rJ   r   rY   rW   rz   r{   r�   r�   )r_   r�   r�   r�   r�   r�   r�   r�   r�   r�   r�   r�   s               ra   �process_end_eventz&ChannelWatchingAlert.process_end_event#  s  � �	�
0	H��G�J�r��r�N�G�G�G�{�[�[�[�[�  �/�;�;�J�G�G�L�� 'Y�+�/�/���C�C��+�/�/��8I�J�J��!-�!1�!1�(�B�!?�!?�� ^��(8�(8��	�(J�(J�  ^�  ^�P\�P`�P`�ai�jl�Pm�Pm�  ^�  ^�  |H�  |L�  |L�  MU�  V[�  |\�  |\�  ^�  ^�� &�)�)�(�5�9�9���U�?�?�v�1A�'A�'A��#8��#8�#8�8�K� "�%�%�d�5�1�1����;�;�2��#5�#5��=�B�=�=�0�K� �K�|�4�4�4�4� �,� 
^�".�"2�"2�8�=M�"N�"N�K��`�k�`�`�Z�XZ�YZ�XZ�^�`�`�`�hs�t�t�t�t� �'�8�8��Z�H�H�H�$(�$7�$H�$H�$J�$J�M� �H��H�H�P\�]�]�]�]� �$�3�3�J�?�?�?��O�z�"�1�"�~�O�O�O�Wb�c�c�c�c�c�c��D��B�Q�B��D�D�D�K�X�X�X�X�X�X��� 	H� 	H� 	H��2�q�2�2�,�G�G�G�G�G�G�G�G�G�G�����	H���s   �G=H& �"H& �&
I�0I�Ic                 ��  � 	 	 | �                     | j        j        dd��  �        }| �                    | j        j        d��  �        }| �                    | j        j        d��  �        }| j        r| j        �                    �   �          | �	                    dt          |�  �        t          |�  �        t          |�  �        d��	�  �         d S # t          $ r}t          d
|� ��  �         Y d }~d S d }~ww xY w)Ni@8  �	timestamp)�ttl�timestamp_keyi,  )r�   i�Q r   )�sessions�events�notifications)�	component�removedzError in cleanup: )�cleanup_dict_by_timerH   r�   �cleanup_dict_by_timestamp�processing_events�notification_historyrY   rW   �cleanup_stale_sessions�log_cleanup_resultsr�   r�   r   )r_   �removed_sessions�removed_events�removed_notificationsr�   s        ra   �run_cleanupz ChannelWatchingAlert.run_cleanup[  sM  � �	�"$	*�#�8�8��$�4��)�  9�  �  �� "�;�;��$�6�� <� � �N� %)�$B�$B��$�9�� %C� %� %�!� �(� =��#�:�:�<�<�<� �$�$�0� #�$4� 5� 5�!�.�1�1�%(�)>�%?�%?�� � %� � � � � �� � 	*� 	*� 	*��(�Q�(�(�)�)�)�)�)�)�)�)�)�����	*���s   �CC �
C2�C-�-C2c                 �   � 	 	 | �                     �   �          d S # t          $ r}t          d|� ��  �         Y d }~d S d }~ww xY w)NzError cleaning up sessions: )r�   r�   r   )r_   r�   s     ra   �cleanupzChannelWatchingAlert.cleanup�  sj   � �B�	4����������� 	4� 	4� 	4��2�q�2�2�3�3�3�3�3�3�3�3�3�����	4���s   � �
A �;�A c                 �B   � 	 	 | �                     �   �          d S #  Y d S xY wrd   )�stop_cleanuprg   s    ra   �__del__zChannelWatchingAlert.__del__�  s2   � �:�	����������	��D�D���s   � �r�   c                 ��   � 	 | j         �                    |�  �        sdS | j         �                    |�  �        }|�                    di �  �        }|�                    d�  �        }||k    S )NTr�   r�   )rH   r�   r�   rJ   )r_   r�   r�   r�   r�   r�   s         ra   r   z$ChannelWatchingAlert._is_new_session�  sp   � �Q��#�/�/�
�;�;� 	��4��+�7�7�
�C�C��'�+�+�N�B�?�?��-�1�1�(�;�;�� "�^�3�3rb   )rm   N)�__name__�
__module__�__qualname__�
ALERT_TYPE�DESCRIPTIONrG   rh   rf   r�   r   r   �boolrp   r�   ro   r�   r�   r�   r�   r�   r�   r   � rb   ra   r   r       s�  � � � � � �.� $�J�=�K�F
� F
� F
�R'� '� '�3� 3� 3�

?�s� 
?��S�#�X�� 
?�SW� 
?� 
?� 
?� 
?�H�� H��c�3�h�� H�D� H� H� H� H�T
�S� 
�d�3��8�n� 
�QU� 
� 
� 
� 
�8]�$�s�C�x�.� ]�PS� ]�X\� ]� ]� ]� ]�~S��S�#�X�� S�4� S� S� S� S�j6H�C� 6H�D� 6H� 6H� 6H� 6H�p6*� 6*� 6*� 6*�p4� 4� 4� 4�� � �
4�#� 
4�s� 
4�t� 
4� 
4� 
4� 
4� 
4� 
4rb   r   )&�	threadingrQ   rI   �typingr   r   r   r   �pytz�baser   �common.session_managerr	   �common.alert_formatterr
   �common.cleanup_mixinr   �common.stream_trackerr   �helpers.loggingr   r   r   �helpers.parsingr   r   r   r   r   r   r   �helpers.channel_infor   �helpers.program_infor   �Lockry   r   r�   rb   ra   �<module>r     s�  ��� � � � � ���� 	�	�	�	� &� &� &� &� &� &� &� &� &� &� � � � � � � ���� � � � � � � 2� 2� 2� 2� 2� 2� 2� 2� 2� 2� 2� 2� .� .� .� .� .� .� 0� 0� 0� 0� 0� 0� <� <� <� <� <� <� <� <� <� <�� � � � � � � � � � � � � � � � � � 7� 6� 6� 6� 6� 6� 6� 6� 6� 6� 6� 6� �Y�^���
�M
4� M
4� M
4� M
4� M
4�9�l� M
4� M
4� M
4� M
4� M
4rb   