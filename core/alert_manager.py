�
    ��gv  �                   �j   � 	 d dl Z d dlZd dlmZmZmZmZ ddlmZ ddl	m
Z
mZmZ  G d� d�  �        ZdS )�    N)�Dict�Any�Optional�List�   )�get_alert_class)�log�LOG_STANDARD�LOG_VERBOSEc                   �   � e Zd Z	 d� Zd� Zd� Zd� Zdedefd�Z	de
e         fd�Zd	ed
eeef         dee         fd�ZdS )�AlertManagerc                 �   � 	 || _         i | _        d| _        t          j        �   �         | _        | �                    �   �          d S )Ni  )�notification_manager�alert_instances�cleanup_interval�time�last_cleanup�_start_cleanup_thread)�selfr   s     �core/alert_manager.py�__init__zAlertManager.__init__   sC   � �+�$8��!�!��� $��� �I�K�K����"�"�$�$�$�$�$�    c                 �f   � 	 t          j        | j        d��  �        }|�                    �   �          d S )NT)�target�daemon)�	threading�Thread�_cleanup_loop�start)r   �cleanup_threads     r   r   z"AlertManager._start_cleanup_thread   s5   � �L�"�)��1C�D�Q�Q�Q���������r   c                 �   � 	 	 t          j        d�  �         t          j         �   �         }|| j        z
  | j        k    r| �                    �   �          || _        �V)NT�<   )r   �sleepr   r   �_run_cleanup)r   �current_times     r   r   zAlertManager._cleanup_loop   sW   � �B�	1��J�r�N�N�N��9�;�;�L��d�/�/�4�3H�H�H��!�!�#�#�#�$0��!�	1r   c                 �J  � 	 	 t          dt          ��  �         | j        �                    �   �         D ]F\  }}	 |�                    �   �          �# t
          $ r}t          d|� d|� ��  �         Y d }~�?d }~ww xY wd S # t
          $ r}t          d|� ��  �         Y d }~d S d }~ww xY w)Nz#Running periodic alert data cleanup��levelzError cleaning up �: zError in cleanup: )r	   r   r   �items�cleanup�	Exception)r   �
alert_type�alert_instance�es       r   r$   zAlertManager._run_cleanup%   s  � �1�	*��5�[�I�I�I�I�.2�.B�.H�.H�.J�.J� @� @�*�
�N�@�"�*�*�,�,�,�,�� � @� @� @��>�Z�>�>�1�>�>�?�?�?�?�?�?�?�?�����@����@� @��
 � 	*� 	*� 	*��(�Q�(�(�)�)�)�)�)�)�)�)�)�����	*���s@   �4A; �A�A; �
A6�A1�,A; �1A6�6A; �;
B"�B�B"r-   �returnc                 ��   � 	 	 t          |�  �        }|r || j        �  �        | j        |<   dS t          d|� ��  �         dS # t          $ r }t          d|� d|� ��  �         Y d }~dS d }~ww xY w)NTzUnknown alert type: FzError registering r)   )r   r   r   r	   r,   )r   r-   �alert_classr/   s       r   �register_alertzAlertManager.register_alert1   s�   � �	�
	�)�*�5�5�K�� �3>�;�t�?X�3Y�3Y��$�Z�0��t��7�:�7�7�8�8�8��u��� 	� 	� 	��6�Z�6�6�1�6�6�7�7�7��5�5�5�5�5�����	���s   �)A �A �
A,�A'�'A,c                 �P   � 	 t          | j        �                    �   �         �  �        S )N)�listr   �keys)r   s    r   �get_registered_alertsz"AlertManager.get_registered_alertsF   s&   � �	�
 �D�(�-�-�/�/�0�0�0r   �
event_type�
event_datac                 �  � 	 |dk    rd S | j         �                    �   �         D ]g\  }}	 |�                    ||�  �        }|rt          d|� �t          ��  �         |c S �<# t
          $ r}t          d|� d|� ��  �         Y d }~�`d }~ww xY wd S )N�hellozAlert triggered: r'   zError processing r)   )r   r*   �process_eventr	   r   r,   )r   r8   r9   r-   r.   �resultr/   s          r   r<   zAlertManager.process_eventN   s�   � �	� �� � ��4� +/�*>�*D�*D�*F�*F� 	;� 	;�&�J��;�'�5�5�j�*�M�M��� &��8�J�8�8��L�L�L�L�%�%�%�%�&�� � ;� ;� ;��9�
�9�9�a�9�9�:�:�:�:�:�:�:�:�����;���� �ts   �2A�
B�)B�BN)�__name__�
__module__�__qualname__r   r   r   r$   �str�boolr3   r   r7   r   r   r   r<   � r   r   r   r      s�   � � � � � �7�%� %� %�� � �
1� 1� 1�
*� 
*� 
*��� �� � � � �*1�t�C�y� 1� 1� 1� 1��� ��c�3�h�� �H�UX�M� � � � � � r   r   )r   r   �typingr   r   r   r   �alertsr   �helpers.loggingr	   r
   r   r   rC   r   r   �<module>rG      s�   ��� � � � � ���� ,� ,� ,� ,� ,� ,� ,� ,� ,� ,� ,� ,� $� $� $� $� $� $� <� <� <� <� <� <� <� <� <� <�]� ]� ]� ]� ]� ]� ]� ]� ]� ]r   