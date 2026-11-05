import { useEffect, useState } from 'react';
import { Drawer, Text } from '@mantine/core';
import { useReducedMotion } from '@mantine/hooks';
import { useNavigate } from 'react-router-dom';
import { tripwireListUrl } from '../components/links';
import TripwireForm from '../components/TripwireForm';
import TripwireList from './TripwireList';

// Match Laminar's Sheet: move the solid panel, with a separate fading overlay.
const drawerTransition = {
  in: { transform: 'translateX(0)' },
  out: { transform: 'translateX(100%)' },
  transitionProperty: 'transform',
};

export default function NewTripwire() {
  const navigate = useNavigate();
  const reducedMotion = useReducedMotion(false, { getInitialValueInEffect: false });
  const [opened, setOpened] = useState(false);
  useEffect(() => { setOpened(true); }, []);
  const close = () => setOpened(false);

  return <>
    <TripwireList />
    <Drawer opened={opened} onClose={close} position="right" size="min(100%, 40rem)"
      title={<Text fw={600}>New signal</Text>} padding="lg"
      transitionProps={{ transition: drawerTransition, duration: reducedMotion ? 0 : 500,
        exitDuration: reducedMotion ? 0 : 300, timingFunction: 'cubic-bezier(.4,0,.2,1)' }}
      onExitTransitionEnd={() => navigate(tripwireListUrl())}
      overlayProps={{ backgroundOpacity: 0.55,
        transitionProps: { duration: reducedMotion ? 0 : 150, exitDuration: reducedMotion ? 0 : 150,
          timingFunction: 'cubic-bezier(.4,0,.2,1)' } }}
      closeButtonProps={{ 'aria-label': 'Close new signal' }}>
      <TripwireForm mode="create" onCancel={close} />
    </Drawer>
  </>;
}
