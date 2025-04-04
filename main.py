�
    ��gP  �                   �   � 	 d dl Z d dlZd dlZd dlZd dlZddlmZmZ ddlm	Z	m
Z
mZ ddlmZ ddlmZmZmZmZ ddlmZ dad� Zd	� Zed
k    r e�   �          dS dS )�    N�   )�__version__�__app_name__)�log�set_log_level�setup_logging)�monitor_event_stream)�check_server_connectivity�initialize_notifications�initialize_alerts�initialize_event_monitor)�run_testc            	      ��
  � 	 t          j        t          � d���  �        } | �                    ddd��  �         | �                    dt          dd	�
�  �         | �                    ddd��  �         | �                    dt
          dd�
�  �         | �                    ddd��  �         | �                    �   �         }t          j        dd�  �        }t          t          j        dd�  �        �  �        }|j	        p|j
        p|j        p|j        d u}t          |||��  �         t          t          j        dd�  �        �  �        }|dvrd}t          ||��  �         |st          dt          � dt           � ��  �         t          j        d�  �        }t          t          j        dd�  �        �  �        }|s#t          d �  �         t#          j        d�  �         |j	        r't#          j        t'          d!||�  �        rd"nd�  �         |j
        r't#          j        t'          d#||�  �        rd"nd�  �         |j        r0|j        }t#          j        t'          d$||d |�  �        rd"nd�  �         t)          |��  �        }	|	s#t          d%�  �         t#          j        d�  �         t+          |	|��  �        }
|j        r/|
r-t#          j        t'          |j        |||
�  �        rd"nd�  �         t-          ||�  �        }|s�t          d&�  �         t          d'�  �         |j        rpt          d(�  �         t          d)�  �         t          d*�  �         t          d+�  �         t          d,�  �         t          d-�  �         	 t1          j        d/�  �         �|st#          j        d�  �         t5          |||
�  �        }|s#t          d0�  �         t#          j        d�  �         t7          j        t6          j        t:          �  �         t7          j        t6          j        t:          �  �         |rWd1|
j        v rN|
j        d1         }tA          |d2�  �        r1tC          tE          |d2�  �        �  �        r|�#                    �   �          t1          j        d�  �         |
j        �$                    �   �         D ]F\  }}tA          |d3�  �        r1tC          tE          |d3�  �        �  �        r|�%                    �   �          �G|
j        �$                    �   �         D ]F\  }}tA          |d4�  �        r1tC          tE          |d4�  �        �  �        r|�&                    �   �          �G|�'                    �   �          d S )5Nz - Channels DVR monitoring tool)�descriptionz--test-connectivity�
store_truezTest API connectivity and exit)�action�helpz--test-alert�
ALERT_TYPEz5Test alert functionality for the specified alert type)�type�metavarr   z
--test-apizTest common API endpointsz--monitor-events�SECONDSz3Monitor event stream for specified seconds and exitz--stay-alivez2Keep container running even with connection errors�CONFIG_PATHz/config�LOG_RETENTION_DAYS�7)�	test_mode�	LOG_LEVEL�1)r   �   r   z	Starting z v�CHANNELS_DVR_HOST�CHANNELS_DVR_PORT�8089z5ERROR: CHANNELS_DVR_HOST environment variable not set�connectivityr   �api�event_streamz/ERROR: Failed to initialize notification systemz(ERROR: Failed to connect to Channels DVRzAPlease verify CHANNELS_DVR_HOST and CHANNELS_DVR_PORT are correctz:Container is now in standby mode due to connection failurezTo resume normal operation:z01. Verify the Channels DVR host address and portz&2. Update your configuration if neededz3. Restart the containerz@Tests can still be run using 'docker exec' while in standby modeTi  z)ERROR: Failed to initialize event monitorz
Disk-Space�log_storage_info�_cache_channels�set_startup_complete)(�argparse�ArgumentParserr   �add_argument�str�int�
parse_args�os�getenv�test_connectivity�test_api�
test_alert�monitor_eventsr   r   r   r   �sys�exitr   r   r   r
   �
stay_alive�time�sleepr   �signal�SIGTERM�signal_handler�SIGINT�alert_instances�hasattr�callable�getattrr%   �itemsr&   r'   �start_monitoring)�parser�args�
config_dir�retention_daysr   �	log_level�host�port�duration�notification_manager�alert_manager�	connected�event_monitor�disk_space_alert�
alert_type�alerts                   �main.py�mainrS      s�  � �/��$�L�1a�1a�1a�b�b�b�F�
���-�l�Ii��j�j�j�
����S�,�  NE��  F�  F�  F�
����\�@[��\�\�\�
���*��i�  OD��  E�  E�  E�
����|�Bv��w�w�w������D� ��=�)�4�4�J����#7��=�=�>�>�N� �&�m�$�-�m�4�?�m�d�Na�im�Nm�I� �*�n�	�B�B�B�B� �B�I�k�3�/�/�0�0�I������	��)�y�1�1�1�1� � 7��5��5�5��5�5�6�6�6� �9�(�)�)�D��r�y�,�f�5�5�6�6�D�� ��C�D�D�D������� �� C���h�~�t�T�:�:�A����B�B�B��}� :���h�u�d�D�1�1�8���q�9�9�9��� S��&����h�~�t�T�4��J�J�Q���PQ�R�R�R� 4�i�H�H�H��� ��=�>�>�>������� &�&:�i�P�P�P�M� �� S�=� S���h�t���d�M�J�J�Q���PQ�R�R�R� *�$��5�5�I�� ��6�7�7�7��O�P�P�P��?� 	��L�M�M�M��-�.�.�.��B�C�C�C��8�9�9�9��*�+�+�+��R�S�S�S�!��
�4� � � �!�� 	��H�Q�K�K�K� -�T�4��G�G�M�� ��7�8�8�8������� �M�&�.�.�1�1�1�
�M�&�-��0�0�0� � 0�\�]�%B�B�B�(�8��F���#�%7�8�8� 	0�X�g�N^�`r�Fs�Fs�=t�=t� 	0��-�-�/�/�/� 	�J�q�M�M�M� +�:�@�@�B�B� $� $��
�E��5�+�,�,� 	$��'�%�IZ�:[�:[�1\�1\� 	$��!�!�#�#�#�� +�:�@�@�B�B� )� )��
�E��5�0�1�1� 	)�h�w�u�Nd�?e�?e�6f�6f� 	)��&�&�(�(�(�� �"�"�$�$�$�$�$�    c                 �   � 	 t          d�  �         t          r dt          _        t          j        d�  �         t          j        d�  �         d S )Nz%Received shutdown signal, stopping...Fg      �?r   )r   rN   �runningr7   r8   r4   r5   )�sig�frames     rR   r;   r;   �   sB   � �%��/�0�0�0�� � %����
�3�����H�Q�K�K�K�K�KrT   �__main__)r.   r4   r7   r(   r9   � r   r   �helpers.loggingr   r   r   �helpers.toolsr	   �helpers.initializer
   r   r   r   �testr   rN   rS   r;   �__name__� rT   rR   �<module>ra      s  ��� 
�	�	�	� 
�
�
�
� ���� ���� ���� '� '� '� '� '� '� '� '� >� >� >� >� >� >� >� >� >� >� /� /� /� /� /� /�� � � � � � � � � � � � � � � � � ���q%� q%� q%�f� � � �z����D�F�F�F�F�F� �rT   