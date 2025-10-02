📖 Explanation of the Ansible Playbook: Push SSH Key
---
- name: Push SSH key to lambda hosts
  hosts: lambda
  gather_facts: no
  vars:
    ansible_user: sysadmin   # adjust if remote user differs


name: Describes the playbook purpose.

hosts: lambda: Runs on all hosts in the lambda inventory group.

gather_facts: no: Skips gathering system facts for speed (not needed for this task).

vars: Sets ansible_user to sysadmin (the remote user you’ll log in as). Adjust if your target host user is different.


Install authorized key for sysadmin

    - name: Install authorized key for sysadmin
      ansible.posix.authorized_key:
        user: sysadmin
        state: present
        key: "{{ lookup('file', '~/.ssh/id_ed25519.pub') }}"


ansible.posix.authorized_key module manages SSH authorized keys for a user.

user: sysadmin specifies which remote user to add the key for.

state: present ensures the key is added (not removed).

key: "{{ lookup('file', '~/.ssh/id_ed25519.pub') }}" reads your local public key file and installs it on the remote host.